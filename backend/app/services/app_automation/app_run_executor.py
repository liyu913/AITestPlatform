"""Execute an AppRun (single case for MVP)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing, to_beijing_iso
from app.models.app_automation.app_automation import AppCase, AppDevice, AppElement, AppEnvironment, AppRun, AppRunStep
from app.services.api_automation import api_env_secrets
from app.services.app_automation.app_device_pool import (
    claim_device,
    device_snapshot,
    merge_env_with_device,
    release_device,
    resolve_required_tags,
)
from app.services.app_automation.app_runner import execute_app_steps
from app.services.file_storage import save_app_run_screenshot

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return to_beijing_iso(now_beijing())


def _build_ctx(env: AppEnvironment | None, account_name: str | None = None) -> dict[str, str]:
    ctx: dict[str, str] = {}
    if env is None:
        return ctx
    if isinstance(env.variables_json, dict):
        ctx.update({str(k): str(v) for k, v in env.variables_json.items()})
    ctx.update(api_env_secrets.decode_secrets(env.secrets_encrypted))
    if account_name and isinstance(env.accounts_json, list):
        for acc in env.accounts_json:
            if not isinstance(acc, dict):
                continue
            if str(acc.get("name") or "") == account_name:
                vars_ = acc.get("variables") if isinstance(acc.get("variables"), dict) else {}
                ctx.update({str(k): str(v) for k, v in vars_.items()})
                break
    return ctx


def _load_env(db: Session, project_id: int, env_id: int | None) -> AppEnvironment | None:
    if env_id is None:
        return (
            db.query(AppEnvironment)
            .filter(AppEnvironment.project_id == project_id, AppEnvironment.is_default.is_(True))
            .first()
        )
    return (
        db.query(AppEnvironment)
        .filter(AppEnvironment.id == env_id, AppEnvironment.project_id == project_id)
        .first()
    )


def _element_map(db: Session, project_id: int) -> dict[str, dict[str, str]]:
    rows = db.query(AppElement).filter(AppElement.project_id == project_id).all()
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        meta = {"strategy": r.strategy or "id", "selector": r.selector}
        key = f"{r.page_name}.{r.name}" if r.page_name else r.name
        out[key] = meta
        out[f"{r.page_name}.{r.name}".strip(".")] = meta
    return out


def _env_dict(env: AppEnvironment) -> dict:
    return {
        "platform": env.platform or "android",
        "appium_server_url": env.appium_server_url or "http://127.0.0.1:4723",
        "device_name": env.device_name or "",
        "udid": env.udid or "",
        "platform_version": env.platform_version or "",
        "automation_name": env.automation_name or "UiAutomator2",
        "app_package": env.app_package or "",
        "app_activity": env.app_activity or "",
        "bundle_id": env.bundle_id or "",
        "app_path": env.app_path or "",
        "no_reset": bool(env.no_reset),
        "full_reset": bool(env.full_reset),
        "caps": env.caps_json if isinstance(env.caps_json, dict) else {},
    }


def _running_count(db: Session, project_id: int, env_id: int | None) -> int:
    q = db.query(AppRun).filter(
        AppRun.project_id == project_id,
        AppRun.status == "running",
    )
    if env_id is not None:
        q = q.filter(AppRun.env_id == env_id)
    return q.count()


def _pool_has_devices(db: Session, project_id: int, platform: str) -> bool:
    return (
        db.query(AppDevice)
        .filter(AppDevice.project_id == project_id, AppDevice.platform == platform)
        .count()
        > 0
    )


def execute_app_run(db: Session, run_id: int) -> None:
    run = db.get(AppRun, run_id)
    if run is None:
        log.warning("app run %s not found", run_id)
        return
    if run.status not in ("pending", "running"):
        return

    env = _load_env(db, run.project_id, run.env_id)
    max_c = int(getattr(env, "max_concurrency", 1) or 1) if env else 1
    others = _running_count(db, run.project_id, run.env_id)
    if run.status == "pending" and others >= max_c:
        log.info("app run %s deferred: concurrency %s/%s", run_id, others, max_c)
        run.summary_json = {
            **(run.summary_json if isinstance(run.summary_json, dict) else {}),
            "deferred": True,
            "reason": "concurrency_limit",
        }
        db.commit()
        return

    claimed: AppDevice | None = None
    platform = (env.platform if env else run.platform) or "android"
    use_pool = bool(getattr(env, "use_device_pool", True)) if env else True
    if use_pool and (run.device_id is not None or _pool_has_devices(db, run.project_id, platform)):
        tags = resolve_required_tags(run, env)
        claimed = claim_device(
            db,
            project_id=run.project_id,
            platform=platform,
            device_id=run.device_id,
            required_tags=tags,
            run_id=run.id,
        )
        if claimed is None:
            # Prefer defer when pool exists but all busy; hard-fail when specific device required
            if run.device_id is not None:
                run.status = "error"
                run.started_at = _utc_now_iso()
                run.finished_at = _utc_now_iso()
                run.summary_json = {
                    "passed": 0,
                    "failed": 0,
                    "error": 1,
                    "skipped": 0,
                    "reason": "device_unavailable",
                }
                db.add(
                    AppRunStep(
                        run_id=run.id,
                        project_id=run.project_id,
                        order=0,
                        status="error",
                        error_message=f"指定设备不可用或非 online: device_id={run.device_id}",
                    )
                )
                db.commit()
                return
            run.summary_json = {
                **(run.summary_json if isinstance(run.summary_json, dict) else {}),
                "deferred": True,
                "reason": "no_free_device",
            }
            db.commit()
            return
        run.device_id = claimed.id
        run.device_snapshot_json = device_snapshot(claimed)

    run.status = "running"
    run.started_at = _utc_now_iso()
    if env:
        run.queue_name = env.queue_name or "app"
        run.platform = env.platform or "android"
    if claimed:
        run.platform = claimed.platform or run.platform
    db.commit()

    try:
        if env is None:
            db.add(
                AppRunStep(
                    run_id=run.id,
                    project_id=run.project_id,
                    order=0,
                    action="",
                    selector="",
                    status="error",
                    error_message="未配置 APP 环境",
                )
            )
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {"passed": 0, "failed": 0, "error": 1, "skipped": 0}
            db.commit()
            return

        if run.account_name:
            accounts = env.accounts_json if isinstance(env.accounts_json, list) else []
            names = {str(a.get("name")) for a in accounts if isinstance(a, dict)}
            if run.account_name not in names:
                db.add(
                    AppRunStep(
                        run_id=run.id,
                        project_id=run.project_id,
                        order=0,
                        status="error",
                        error_message=f"环境账号不存在: {run.account_name}",
                    )
                )
                run.status = "error"
                run.finished_at = _utc_now_iso()
                run.summary_json = {"passed": 0, "failed": 0, "error": 1, "skipped": 0}
                db.commit()
                return

        case = (
            db.query(AppCase)
            .filter(AppCase.id == run.target_id, AppCase.project_id == run.project_id)
            .first()
        )
        if case is None:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {"error": "APP 用例不存在或不属于该项目"}
            db.commit()
            return

        ctx = _build_ctx(env, run.account_name)
        elements = _element_map(db, run.project_id)
        default_timeout = int(env.default_timeout_ms or 15000)
        default_retries = int(env.step_retries if env.step_retries is not None else 1)
        project_id = run.project_id
        rid = run.id
        steps = case.steps_json if isinstance(case.steps_json, list) else []
        passed = failed = error = skipped = 0

        def _save_shot(data: bytes, filename: str) -> str:
            key, _ = save_app_run_screenshot(project_id, rid, filename, data)
            return key

        runner_env = merge_env_with_device(_env_dict(env), claimed)

        try:
            step_results = execute_app_steps(
                env=runner_env,
                steps=steps,
                ctx=ctx,
                default_timeout_ms=default_timeout,
                default_retries=default_retries,
                elements=elements,
                screenshot_saver=_save_shot,
            )
        except Exception as ex:  # noqa: BLE001
            log.exception("app run %s case %s crash", run_id, case.id)
            db.add(
                AppRunStep(
                    run_id=run.id,
                    project_id=run.project_id,
                    order=0,
                    case_id=case.id,
                    case_name=case.name,
                    action="",
                    selector="",
                    status="error",
                    error_message=f"执行异常: {ex}",
                )
            )
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {
                "passed": 0,
                "failed": 0,
                "error": 1,
                "skipped": 0,
                "platform": run.platform,
                "device_id": run.device_id,
            }
            db.commit()
            return

        step_order = 0
        case_failed = False
        case_error = False
        if not step_results:
            passed += 1
        for r in step_results:
            st = r.get("status") or "error"
            if st == "passed":
                pass
            elif st == "failed":
                case_failed = True
            else:
                case_error = True
            db.add(
                AppRunStep(
                    run_id=run.id,
                    project_id=run.project_id,
                    order=step_order,
                    case_id=case.id,
                    case_name=case.name,
                    action=str(r.get("action") or ""),
                    selector=str(r.get("selector") or ""),
                    status=st,
                    error_message=str(r.get("error_message") or ""),
                    duration_ms=r.get("duration_ms"),
                    screenshot_key=r.get("screenshot_key"),
                    detail_json=r.get("detail_json"),
                    retry_count=int(r.get("retry_count") or 0),
                )
            )
            step_order += 1

        if case_error:
            error += 1
        elif case_failed:
            failed += 1
        else:
            passed += 1

        summary = {
            "passed": passed,
            "failed": failed,
            "error": error,
            "skipped": skipped,
            "platform": run.platform,
            "account": run.account_name,
            "device_id": run.device_id,
        }
        if error:
            run.status = "error"
        elif failed:
            run.status = "failed"
        else:
            run.status = "passed"
        run.summary_json = summary
        run.finished_at = _utc_now_iso()
        db.commit()
    finally:
        release_device(db, run.device_id if run else None, run_id=run_id)
        db.commit()
