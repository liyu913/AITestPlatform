"""Execute an ApiRun for a single case, a batch of independent cases, or a suite."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing, to_beijing_iso
from app.models.api_automation.api_automation import ApiCase, ApiEnvironment, ApiRun, ApiRunStep, ApiSuite
from app.services.api_automation import api_env_secrets
from app.services.api_automation.api_runner import execute_http_case
from app.services.api_automation.api_suite_steps import (
    eval_when,
    merge_assertions,
    merge_extractors,
    normalize_steps,
    step_has_inline_request,
)

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


def _merge_headers_maps(env: ApiEnvironment | None, step_headers: dict[str, str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if env and isinstance(env.headers_json, dict):
        headers.update({str(k): str(v) for k, v in env.headers_json.items()})
    if isinstance(step_headers, dict):
        headers.update({str(k): str(v) for k, v in step_headers.items()})
    return headers


def _load_env(db: Session, project_id: int, env_id: int | None) -> ApiEnvironment | None:
    if env_id is None:
        return None
    return (
        db.query(ApiEnvironment)
        .filter(ApiEnvironment.id == env_id, ApiEnvironment.project_id == project_id)
        .first()
    )


def _persist_run_step(
    *,
    db: Session,
    run: ApiRun,
    order: int,
    step_key: str | None,
    case_id: int | None,
    case_name: str,
    result: dict[str, Any],
) -> ApiRunStep:
    step = ApiRunStep(
        run_id=run.id,
        project_id=run.project_id,
        order=order,
        step_key=step_key,
        case_id=case_id,
        case_name=case_name,
        request_snapshot_json=result.get("request_snapshot"),
        response_status=result.get("response_status"),
        response_headers_json=result.get("response_headers"),
        response_body_text=result.get("response_body") or "",
        duration_ms=result.get("duration_ms"),
        assertion_results_json=result.get("assertion_results") or [],
        extract_results_json=result.get("extract_results") or {},
        status=result.get("status") or "error",
        error_message=result.get("error_message") or "",
    )
    db.add(step)
    return step


def _run_one_case(
    *,
    db: Session,
    run: ApiRun,
    env: ApiEnvironment,
    case: ApiCase,
    order: int,
    ctx: dict[str, str],
    step_key: str | None = None,
    extract_overrides: list | None = None,
    assert_overrides: list | None = None,
) -> ApiRunStep:
    case_extractors = case.extractors_json if isinstance(case.extractors_json, list) else []
    extractors = merge_extractors(case_extractors, extract_overrides if isinstance(extract_overrides, list) else None)
    case_assertions = case.assertions_json if isinstance(case.assertions_json, list) else []
    assertions = merge_assertions(case_assertions, assert_overrides if isinstance(assert_overrides, list) else None)
    case_headers = case.headers_json if isinstance(case.headers_json, dict) else {}
    result = execute_http_case(
        method=case.method,
        base_url=env.base_url,
        path=case.path,
        query=case.query_json if isinstance(case.query_json, dict) else {},
        headers=_merge_headers_maps(env, {str(k): str(v) for k, v in case_headers.items()}),
        body_type=case.body_type or "none",
        body_text=case.body_text or "",
        timeout_ms=case.timeout_ms or 15000,
        assertions=assertions,
        extractors=extractors,
        ctx=ctx,
        pre_script=case.pre_script,
        post_script=case.post_script,
    )
    return _persist_run_step(
        db=db,
        run=run,
        order=order,
        step_key=step_key,
        case_id=case.id,
        case_name=case.name,
        result=result,
    )


def _run_inline_step(
    *,
    db: Session,
    run: ApiRun,
    env: ApiEnvironment,
    node: dict[str, Any],
    order: int,
    ctx: dict[str, str],
) -> ApiRunStep:
    step_headers = node.get("headers") if isinstance(node.get("headers"), dict) else {}
    extractors = node.get("extractors") if isinstance(node.get("extractors"), list) else []
    # legacy overrides still merge on top of inline extractors
    if isinstance(node.get("extract_overrides"), list):
        extractors = merge_extractors(extractors, node.get("extract_overrides"))
    assertions = node.get("assertions") if isinstance(node.get("assertions"), list) else []
    if isinstance(node.get("assert_overrides"), list):
        assertions = merge_assertions(assertions, node.get("assert_overrides"))
    result = execute_http_case(
        method=str(node.get("method") or "GET"),
        base_url=env.base_url,
        path=str(node.get("path") or "/"),
        query=node.get("query") if isinstance(node.get("query"), dict) else {},
        headers=_merge_headers_maps(env, {str(k): str(v) for k, v in step_headers.items()}),
        body_type=str(node.get("body_type") or "none"),
        body_text=str(node.get("body") or ""),
        timeout_ms=int(node.get("timeout_ms") or 15000),
        assertions=assertions,
        extractors=extractors,
        ctx=ctx,
        pre_script=None,
        post_script=None,
    )
    name = str(
        node.get("name")
        or node.get("definition_name")
        or node.get("case_name")
        or f"{node.get('method')} {node.get('path')}"
    )
    case_id = None
    if node.get("case_id") is not None:
        try:
            case_id = int(node["case_id"])
        except (TypeError, ValueError):
            case_id = None
    return _persist_run_step(
        db=db,
        run=run,
        order=order,
        step_key=str(node.get("id") or ""),
        case_id=case_id,
        case_name=name,
        result=result,
    )


def _execute_suite_steps(
    *,
    db: Session,
    run: ApiRun,
    env: ApiEnvironment,
    suite: ApiSuite,
    ctx: dict[str, str],
) -> dict[str, int]:
    case_ids = suite.case_ids_json if isinstance(suite.case_ids_json, list) else []
    steps = normalize_steps(
        suite.steps_json if isinstance(suite.steps_json, list) else None,
        [int(x) for x in case_ids],
        stop_on_fail=bool(suite.stop_on_fail),
    )
    by_id = {str(s["id"]): s for s in steps}
    case_cache: dict[int, ApiCase | None] = {}

    def load_case(cid: int) -> ApiCase | None:
        if cid not in case_cache:
            case_cache[cid] = (
                db.query(ApiCase)
                .filter(ApiCase.id == cid, ApiCase.project_id == run.project_id)
                .first()
            )
        return case_cache[cid]

    passed = failed = error = skipped = 0
    order = 0
    cur = str(steps[0]["id"]) if steps else "end"
    last_status: int | None = None
    max_hops = max(len(steps) * 3, 10)
    hops = 0

    while cur != "end" and hops < max_hops:
        hops += 1
        node = by_id.get(cur)
        if node is None:
            error += 1
            db.add(
                ApiRunStep(
                    run_id=run.id,
                    project_id=run.project_id,
                    order=order,
                    step_key=cur,
                    case_name=f"missing-step:{cur}",
                    status="error",
                    error_message=f"步骤 {cur} 不存在",
                )
            )
            break

        step_key = str(node["id"])
        inline = step_has_inline_request(node)
        case_id: int | None = None
        if node.get("case_id") is not None:
            try:
                case_id = int(node["case_id"])
            except (TypeError, ValueError):
                case_id = None
        case = load_case(case_id) if case_id is not None else None
        display_name = str(
            node.get("name")
            or node.get("definition_name")
            or (case.name if case else None)
            or node.get("case_name")
            or step_key
        )

        if not eval_when(node.get("when"), ctx=ctx, last_status=last_status):
            db.add(
                ApiRunStep(
                    run_id=run.id,
                    project_id=run.project_id,
                    order=order,
                    step_key=step_key,
                    case_id=case_id,
                    case_name=display_name,
                    status="skipped",
                    error_message="when 条件不满足，已跳过",
                )
            )
            skipped += 1
            order += 1
            cur = str(node.get("on_pass") or "end")
            continue

        if inline:
            step = _run_inline_step(db=db, run=run, env=env, node=node, order=order, ctx=ctx)
        elif case is not None:
            step = _run_one_case(
                db=db,
                run=run,
                env=env,
                case=case,
                order=order,
                ctx=ctx,
                step_key=step_key,
                extract_overrides=node.get("extract_overrides")
                if isinstance(node.get("extract_overrides"), list)
                else None,
                assert_overrides=node.get("assert_overrides")
                if isinstance(node.get("assert_overrides"), list)
                else None,
            )
        else:
            db.add(
                ApiRunStep(
                    run_id=run.id,
                    project_id=run.project_id,
                    order=order,
                    step_key=step_key,
                    case_id=case_id,
                    case_name=display_name,
                    status="error",
                    error_message="步骤缺少可执行请求，且绑定用例不存在",
                )
            )
            error += 1
            order += 1
            cur = str(node.get("on_fail") or "end")
            continue

        order += 1
        last_status = step.response_status
        st = step.status
        # merge extract results into shared ctx for subsequent steps
        if isinstance(step.extract_results_json, dict):
            for k, v in step.extract_results_json.items():
                ctx[str(k)] = str(v)
        if st == "passed":
            passed += 1
            cur = str(node.get("on_pass") or "end")
        elif st == "failed":
            failed += 1
            cur = str(node.get("on_fail") or "end")
        else:
            error += 1
            cur = str(node.get("on_fail") or "end")

    if hops >= max_hops and cur != "end":
        error += 1
        db.add(
            ApiRunStep(
                run_id=run.id,
                project_id=run.project_id,
                order=order,
                step_key=cur,
                case_name="loop-guard",
                status="error",
                error_message="步骤跳转次数超限，可能存在环",
            )
        )

    return {"passed": passed, "failed": failed, "error": error, "skipped": skipped}


def _batch_case_ids(run: ApiRun) -> list[int]:
    raw = run.summary_json.get("case_ids") if isinstance(run.summary_json, dict) else None
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _execute_batch(
    *,
    db: Session,
    run: ApiRun,
    env: ApiEnvironment,
    case_ids: list[int],
) -> dict[str, Any]:
    """Run each case with a fresh env context. A failure does not stop the rest."""
    summary: dict[str, Any] = {
        "passed": 0,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "case_ids": case_ids,
    }
    for order, case_id in enumerate(case_ids):
        case = (
            db.query(ApiCase)
            .filter(ApiCase.id == case_id, ApiCase.project_id == run.project_id)
            .first()
        )
        if case is None:
            _persist_run_step(
                db=db,
                run=run,
                order=order,
                step_key=None,
                case_id=case_id,
                case_name=f"#{case_id}",
                result={"status": "error", "error_message": "接口用例不存在或不属于该项目"},
            )
            summary["error"] += 1
            continue
        step = _run_one_case(
            db=db,
            run=run,
            env=env,
            case=case,
            order=order,
            ctx=_build_ctx(env),
        )
        if step.status == "passed":
            summary["passed"] += 1
        elif step.status == "failed":
            summary["failed"] += 1
        else:
            summary["error"] += 1
    return summary


def execute_api_run(db: Session, run_id: int) -> None:
    run = db.get(ApiRun, run_id)
    if run is None:
        log.warning("api run %s not found", run_id)
        return
    if run.status not in ("pending", "running"):
        return

    run.status = "running"
    run.started_at = _utc_now_iso()
    db.commit()

    env = _load_env(db, run.project_id, run.env_id)
    if env is None or not (env.base_url or "").strip():
        preserved: dict[str, Any] = {}
        if isinstance(run.summary_json, dict) and "case_ids" in run.summary_json:
            preserved["case_ids"] = run.summary_json["case_ids"]
        run.status = "error"
        run.finished_at = _utc_now_iso()
        run.summary_json = {
            "passed": 0,
            "failed": 0,
            "error": 1,
            "message": "未配置环境或 base_url",
            **preserved,
        }
        db.commit()
        return

    ctx = _build_ctx(env)

    if run.trigger_type == "suite":
        suite = (
            db.query(ApiSuite)
            .filter(ApiSuite.id == run.target_id, ApiSuite.project_id == run.project_id)
            .first()
        )
        if suite is None:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {"error": "场景不存在或不属于该项目"}
            db.commit()
            return
        case_ids = suite.case_ids_json if isinstance(suite.case_ids_json, list) else []
        steps = suite.steps_json if isinstance(suite.steps_json, list) else None
        if not case_ids and not steps:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {"passed": 0, "failed": 0, "error": 1, "message": "没有可执行的步骤"}
            db.commit()
            return
        summary = _execute_suite_steps(db=db, run=run, env=env, suite=suite, ctx=ctx)
    elif run.trigger_type == "batch":
        case_ids = _batch_case_ids(run)
        if not case_ids:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {
                "passed": 0,
                "failed": 0,
                "error": 1,
                "message": "没有可执行的用例",
                "case_ids": [],
            }
            db.commit()
            return
        summary = _execute_batch(db=db, run=run, env=env, case_ids=case_ids)
    else:
        case = (
            db.query(ApiCase)
            .filter(ApiCase.id == run.target_id, ApiCase.project_id == run.project_id)
            .first()
        )
        if case is None:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {"error": "接口用例不存在或不属于该项目"}
            db.commit()
            return
        step = _run_one_case(db=db, run=run, env=env, case=case, order=0, ctx=ctx)
        summary = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        if step.status == "passed":
            summary["passed"] = 1
        elif step.status == "failed":
            summary["failed"] = 1
        else:
            summary["error"] = 1

    if summary.get("error"):
        run.status = "error"
    elif summary.get("failed"):
        run.status = "failed"
    else:
        run.status = "passed"
    run.summary_json = summary
    run.finished_at = _utc_now_iso()
    db.commit()
