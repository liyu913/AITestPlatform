"""Execute a PerfRun via Locust."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing, to_beijing_iso
from app.models.api_automation.api_automation import ApiCase, ApiEnvironment
from app.models.perf.perf_automation import PerfRun, PerfScenario
from app.services.api_automation import api_env_secrets
from app.services.perf.perf_runner import build_request_specs, evaluate_sla, run_locust_load

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return to_beijing_iso(now_beijing())


def _build_ctx(env: ApiEnvironment | None) -> dict[str, str]:
    ctx: dict[str, str] = {}
    if env is None:
        return ctx
    if isinstance(env.variables_json, dict):
        ctx.update({str(k): str(v) for k, v in env.variables_json.items()})
    ctx.update(api_env_secrets.decode_secrets(env.secrets_encrypted))
    return ctx


def _env_headers(env: ApiEnvironment | None) -> dict[str, str]:
    if env and isinstance(env.headers_json, dict):
        return {str(k): str(v) for k, v in env.headers_json.items()}
    return {}


def _load_env(db: Session, project_id: int, env_id: int | None) -> ApiEnvironment | None:
    if env_id is None:
        return None
    return (
        db.query(ApiEnvironment)
        .filter(ApiEnvironment.id == env_id, ApiEnvironment.project_id == project_id)
        .first()
    )


def _case_snapshots(db: Session, project_id: int, case_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in case_items:
        cid = int(item.get("case_id") or 0)
        weight = max(1, int(item.get("weight") or 1))
        case = db.query(ApiCase).filter(ApiCase.id == cid, ApiCase.project_id == project_id).first()
        if case is None:
            continue
        out.append(
            {
                "case_id": case.id,
                "name": case.name,
                "method": case.method,
                "path": case.path,
                "query": case.query_json if isinstance(case.query_json, dict) else {},
                "headers": case.headers_json if isinstance(case.headers_json, dict) else {},
                "body_type": case.body_type or "none",
                "body_text": case.body_text or "",
                "timeout_ms": case.timeout_ms or 15000,
                "weight": weight,
            }
        )
    return out


def _is_cancelled(db: Session, run_id: int) -> bool:
    db.expire_all()
    run = db.get(PerfRun, run_id)
    return run is not None and run.status == "cancelled"


def execute_perf_run(db: Session, run_id: int) -> None:
    run = db.get(PerfRun, run_id)
    if run is None:
        log.warning("perf run %s not found", run_id)
        return
    if run.status == "cancelled":
        return
    if run.status not in ("pending", "running"):
        return

    run.status = "running"
    run.started_at = _utc_now_iso()
    db.commit()

    try:
        if _is_cancelled(db, run_id):
            return

        snap = run.config_snapshot_json if isinstance(run.config_snapshot_json, dict) else {}
        env = _load_env(db, run.project_id, run.env_id)
        if env is None or not (env.base_url or "").strip():
            run.status = "error"
            run.summary_json = {"error": "请先配置环境的 base_url"}
            run.finished_at = _utc_now_iso()
            db.commit()
            return

        case_items = snap.get("case_items") if isinstance(snap.get("case_items"), list) else []
        if not case_items and run.scenario_id:
            scenario = db.get(PerfScenario, run.scenario_id)
            if scenario and isinstance(scenario.case_items_json, list):
                case_items = scenario.case_items_json

        cases = _case_snapshots(db, run.project_id, case_items)
        if not cases:
            run.status = "error"
            run.summary_json = {"error": "压测场景中没有有效用例"}
            run.finished_at = _utc_now_iso()
            db.commit()
            return

        users = int(snap.get("users") or 10)
        spawn_rate = float(snap.get("spawn_rate") or 2.0)
        run_time_sec = int(snap.get("run_time_sec") or 60)
        think_time_ms = int(snap.get("think_time_ms") or 0)
        sla = snap.get("sla") if isinstance(snap.get("sla"), dict) else {}

        ctx = _build_ctx(env)
        specs = build_request_specs(
            base_url=env.base_url,
            cases=cases,
            ctx=ctx,
            env_headers=_env_headers(env),
        )
        host = f"{urlparse(env.base_url).scheme}://{urlparse(env.base_url).netloc}"

        metrics = run_locust_load(
            host=host,
            specs=specs,
            users=users,
            spawn_rate=spawn_rate,
            run_time_sec=run_time_sec,
            think_time_ms=think_time_ms,
            should_stop=lambda: _is_cancelled(db, run_id),
        )

        # Re-read in case cancel landed while Locust was quitting
        db.refresh(run)
        if run.status == "cancelled" or metrics.get("cancelled"):
            sla_result = evaluate_sla(metrics, sla)
            run.summary_json = {**metrics, "sla": sla_result, "cancelled": True}
            run.status = "cancelled"
            run.finished_at = _utc_now_iso()
            db.commit()
            return

        sla_result = evaluate_sla(metrics, sla)
        summary = {**metrics, "sla": sla_result}
        run.summary_json = summary
        run.status = "passed" if sla_result.get("passed", True) else "failed"
        run.finished_at = _utc_now_iso()
        db.commit()
    except Exception as ex:
        log.exception("perf run %s failed", run_id)
        db.refresh(run)
        if run.status == "cancelled":
            run.finished_at = run.finished_at or _utc_now_iso()
            db.commit()
            return
        run.status = "error"
        run.summary_json = {"error": str(ex)[:2000]}
        run.finished_at = _utc_now_iso()
        db.commit()
