"""Execute a UiRun (case or suite)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing, to_beijing_iso
from app.models.ui_automation.ui_automation import UiCase, UiElement, UiEnvironment, UiRun, UiRunStep, UiSuite
from app.services.api_automation import api_env_secrets
from app.services.file_storage import save_ui_run_screenshot
from app.services.ui_automation.ui_env_auth import (
    effective_run_auth_mode,
    env_has_inject_config,
    normalize_auth_mode,
    resolve_browser_auth,
)
from app.services.ui_automation.ui_runner import execute_ui_script, execute_ui_steps

log = logging.getLogger(__name__)

_MAX_LOG_LINES = 200


def _utc_now_iso() -> str:
    return to_beijing_iso(now_beijing())


def _build_ctx(
    env: UiEnvironment | None,
    account_name: str | None = None,
    case_variables: dict | None = None,
    run_variables: dict | None = None,
) -> dict[str, str]:
    ctx: dict[str, str] = {}
    if env is not None:
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
    if isinstance(case_variables, dict):
        ctx.update({str(k): str(v) for k, v in case_variables.items() if str(k).strip()})
    if isinstance(run_variables, dict):
        ctx.update({str(k): str(v) for k, v in run_variables.items() if str(k).strip()})
    if not ctx.get("username") and ctx.get("user"):
        ctx["username"] = ctx["user"]
    return ctx


def _run_options(run: UiRun) -> dict[str, Any]:
    summary = run.summary_json if isinstance(run.summary_json, dict) else {}
    opts = summary.get("run_options")
    return opts if isinstance(opts, dict) else {}


def _load_env(db: Session, project_id: int, env_id: int | None) -> UiEnvironment | None:
    if env_id is None:
        return (
            db.query(UiEnvironment)
            .filter(UiEnvironment.project_id == project_id, UiEnvironment.is_default.is_(True))
            .first()
        )
    return (
        db.query(UiEnvironment)
        .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
        .first()
    )


def _element_map(db: Session, project_id: int) -> dict[str, str]:
    rows = db.query(UiElement).filter(UiElement.project_id == project_id).all()
    out: dict[str, str] = {}
    for r in rows:
        key = f"{r.page_name}.{r.name}" if r.page_name else r.name
        out[key] = r.selector
        out[f"{r.page_name}.{r.name}".strip(".")] = r.selector
    return out


def _running_count(db: Session, project_id: int, env_id: int | None) -> int:
    q = db.query(UiRun).filter(
        UiRun.project_id == project_id,
        UiRun.status == "running",
    )
    if env_id is not None:
        q = q.filter(UiRun.env_id == env_id)
    return q.count()


def _dsl_steps(case: UiCase) -> list[dict[str, Any]] | None:
    """Return steps for execute_ui_steps, or None to run case.script.

    Runnable scripts (Node ``async function run`` / Python ``def run``) are the
    source of truth. Agent confirm writes both script and steps_json; preferring
    steps caused about:blank when the first step was click without goto.
    Aligns with script-only PATCH clearing steps_json.
    """
    from app.services.ui_automation.ui_node_script import is_runnable_ui_script

    if is_runnable_ui_script(case.script or ""):
        return None
    raw = case.steps_json if isinstance(case.steps_json, list) else None
    if not raw:
        return None
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action or action == "done":
            continue
        if str(item.get("driver") or "").lower() == "midscene":
            return None
        cleaned.append(item)
    return cleaned or None


def _append_log(run: UiRun, line: str) -> None:
    summary = run.summary_json if isinstance(run.summary_json, dict) else {}
    logs = list(summary.get("log_lines") or []) if isinstance(summary.get("log_lines"), list) else []
    logs.append(str(line)[:1000])
    if len(logs) > _MAX_LOG_LINES:
        logs = logs[-_MAX_LOG_LINES:]
    run.summary_json = {**summary, "log_lines": logs}


def _persist_step(
    db: Session,
    *,
    run: UiRun,
    order: int,
    case: UiCase | None,
    result: dict[str, Any],
    commit: bool = True,
) -> UiRunStep:
    step = UiRunStep(
        run_id=run.id,
        project_id=run.project_id,
        order=order,
        case_id=case.id if case else None,
        case_name=case.name if case else "",
        action=str(result.get("action") or ""),
        selector=str(result.get("selector") or ""),
        status=str(result.get("status") or "error"),
        error_message=str(result.get("error_message") or ""),
        duration_ms=result.get("duration_ms"),
        screenshot_key=result.get("screenshot_key"),
        detail_json=result.get("detail_json") if isinstance(result.get("detail_json"), dict) else None,
        retry_count=int(result.get("retry_count") or 0),
    )
    db.add(step)
    action = str(result.get("action") or "step")
    status = str(result.get("status") or "")
    _append_log(run, f"{action} → {status}")
    if commit:
        db.commit()
        db.refresh(step)
    return step


def execute_ui_run(db: Session, run_id: int) -> None:
    run = db.get(UiRun, run_id)
    if run is None:
        log.warning("ui run %s not found", run_id)
        return
    if run.status not in ("pending", "running"):
        return

    env = _load_env(db, run.project_id, run.env_id)
    max_c = int(getattr(env, "max_concurrency", 1) or 1) if env else 1
    # allow this run: count other running for same env
    others = _running_count(db, run.project_id, run.env_id)
    if run.status == "pending" and others >= max_c:
        # leave pending for later retry by worker tick / re-enqueue
        log.info("ui run %s deferred: concurrency %s/%s", run_id, others, max_c)
        run.summary_json = {
            **(run.summary_json if isinstance(run.summary_json, dict) else {}),
            "deferred": True,
            "reason": "concurrency_limit",
        }
        db.commit()
        return

    run.status = "running"
    run.started_at = _utc_now_iso()
    run.summary_json = {
        **(run.summary_json if isinstance(run.summary_json, dict) else {}),
        "log_lines": [],
        "passed": 0,
        "failed": 0,
        "error": 0,
        "skipped": 0,
    }
    if env:
        run.queue_name = env.queue_name or "ui"
        if not run.browser:
            run.browser = env.browser or "chromium"
    _append_log(run, f"开始执行 run=#{run.id}")
    db.commit()

    if env is None or not (env.base_url or "").strip():
        db.add(
            UiRunStep(
                run_id=run.id,
                project_id=run.project_id,
                order=0,
                action="",
                selector="",
                status="error",
                error_message="未配置环境或 base_url",
            )
        )
        run.status = "error"
        run.finished_at = _utc_now_iso()
        run.summary_json = {
            **(run.summary_json if isinstance(run.summary_json, dict) else {}),
            "passed": 0,
            "failed": 0,
            "error": 1,
            "skipped": 0,
        }
        _append_log(run, "错误：未配置环境或 base_url")
        db.commit()
        return

    accounts = env.accounts_json if isinstance(env.accounts_json, list) else []
    account_names = {str(a.get("name")) for a in accounts if isinstance(a, dict)}
    if run.account_name and run.account_name not in account_names:
        db.add(
            UiRunStep(
                run_id=run.id,
                project_id=run.project_id,
                order=0,
                status="error",
                error_message=f"环境账号不存在: {run.account_name}",
            )
        )
        run.status = "error"
        run.finished_at = _utc_now_iso()
        run.summary_json = {
            **(run.summary_json if isinstance(run.summary_json, dict) else {}),
            "passed": 0,
            "failed": 0,
            "error": 1,
            "skipped": 0,
        }
        _append_log(run, f"错误：环境账号不存在 {run.account_name}")
        db.commit()
        return

    cases: list[UiCase] = []
    stop_on_fail = True
    if run.trigger_type == "suite":
        suite = (
            db.query(UiSuite)
            .filter(UiSuite.id == run.target_id, UiSuite.project_id == run.project_id)
            .first()
        )
        if suite is None:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {
                **(run.summary_json if isinstance(run.summary_json, dict) else {}),
                "error": "套件不存在或不属于该项目",
            }
            db.commit()
            return
        stop_on_fail = bool(suite.stop_on_fail)
        ids = suite.case_ids_json if isinstance(suite.case_ids_json, list) else []
        for cid in ids:
            try:
                case_id = int(cid)
            except (TypeError, ValueError):
                continue
            case = (
                db.query(UiCase)
                .filter(UiCase.id == case_id, UiCase.project_id == run.project_id)
                .first()
            )
            if case is not None:
                cases.append(case)
    else:
        case = (
            db.query(UiCase)
            .filter(UiCase.id == run.target_id, UiCase.project_id == run.project_id)
            .first()
        )
        if case is None:
            run.status = "error"
            run.finished_at = _utc_now_iso()
            run.summary_json = {
                **(run.summary_json if isinstance(run.summary_json, dict) else {}),
                "error": "UI 用例不存在或不属于该项目",
            }
            db.commit()
            return
        cases = [case]

    if not cases:
        run.status = "error"
        run.finished_at = _utc_now_iso()
        run.summary_json = {
            **(run.summary_json if isinstance(run.summary_json, dict) else {}),
            "passed": 0,
            "failed": 0,
            "error": 1,
            "message": "没有可执行的用例",
        }
        db.commit()
        return

    run_opts = _run_options(run)
    run_auth_mode = normalize_auth_mode(str(run_opts.get("auth_mode") or "") or None)
    run_variables = (
        {str(k): str(v) for k, v in run_opts["variables"].items() if str(k).strip()}
        if isinstance(run_opts.get("variables"), dict)
        else None
    )
    viewport = env.viewport_json if isinstance(env.viewport_json, dict) else {"width": 1280, "height": 720}
    elements = _element_map(db, run.project_id)
    browser_name = run.browser or env.browser or "chromium"
    default_timeout = int(env.default_timeout_ms or 15000)
    project_id = run.project_id
    rid = run.id
    step_order = 0
    passed = failed = error = skipped = 0

    def _save_shot(data: bytes, filename: str) -> str:
        key, _ = save_ui_run_screenshot(project_id, rid, filename, data)
        return key

    def _skip_rest(from_index: int, reason: str) -> None:
        nonlocal step_order, skipped
        for rest in cases[from_index:]:
            _persist_step(
                db,
                run=run,
                order=step_order,
                case=rest,
                result={
                    "action": "",
                    "selector": "",
                    "status": "skipped",
                    "error_message": reason,
                    "duration_ms": None,
                    "screenshot_key": None,
                    "detail_json": None,
                    "retry_count": 0,
                },
            )
            skipped += 1
            step_order += 1

    for i, case in enumerate(cases):
        case_auth = effective_run_auth_mode(
            run_auth_mode,
            normalize_auth_mode(getattr(case, "auth_mode", None)),
        )
        # Only bind account when auth is account; follow-env must not steal token inject
        if case_auth == "account":
            case_account = (run.account_name or "").strip() or (
                str(getattr(case, "account_name", None) or "").strip() or None
            )
        else:
            case_account = None
        case_vars = case.variables_json if isinstance(case.variables_json, dict) else None
        # Merge for token lookup: case vars then run overrides (same order as ctx).
        token_vars: dict[str, str] = {}
        if isinstance(case_vars, dict):
            token_vars.update({str(k): str(v) for k, v in case_vars.items() if str(k).strip()})
        if isinstance(run_variables, dict):
            token_vars.update(run_variables)
        if case_auth == "none":
            browser_auth = resolve_browser_auth(
                env,
                auth_mode="none",
                token_vars=token_vars or None,
            )
        elif case_auth in ("account", "token"):
            browser_auth = resolve_browser_auth(
                env,
                auth_mode=case_auth,
                account_name=case_account,
                token_vars=token_vars or None,
            )
        else:
            browser_auth = resolve_browser_auth(
                env,
                apply_env_defaults=True,
                token_vars=token_vars or None,
            )
        if env_has_inject_config(env) and case_auth != "none":
            if not (browser_auth.extra_http_headers or browser_auth.local_storage):
                _append_log(
                    run,
                    f"用例 #{case.id} 环境已配置请求头/localStorage 注入，"
                    "但模板变量未解析出值（请检查环境变量/密钥与 {{...}}）",
                )
            else:
                keys: list[str] = []
                if browser_auth.extra_http_headers:
                    keys.append("headers=" + ",".join(browser_auth.extra_http_headers.keys()))
                if browser_auth.local_storage:
                    keys.append("localStorage=" + ",".join(browser_auth.local_storage.keys()))
                _append_log(run, f"用例 #{case.id} 已注入 " + "；".join(keys))
        storage_state_path = browser_auth.materialize_storage_path()
        ctx = _build_ctx(
            env,
            case_account,
            case_vars,
            run_variables,
        )
        dsl = _dsl_steps(case)
        script = (case.script or "").strip()
        if not script and not dsl:
            legacy = case.steps_json if isinstance(case.steps_json, list) else []
            if legacy:
                from app.services.ui_automation.ui_ai_explorer import steps_to_script

                script = steps_to_script(legacy, elements=elements).strip()

        if not dsl and not script:
            _persist_step(
                db,
                run=run,
                order=step_order,
                case=case,
                result={
                    "action": "script",
                    "selector": "",
                    "status": "error",
                    "error_message": "用例没有可执行脚本",
                    "duration_ms": None,
                    "screenshot_key": None,
                    "detail_json": None,
                    "retry_count": 0,
                },
            )
            error += 1
            step_order += 1
            if stop_on_fail:
                _skip_rest(i + 1, "前序用例错误已跳过")
                break
            continue

        _append_log(run, f"用例 #{case.id} {case.name} 开始")
        db.commit()

        case_failed = False
        case_error = False
        step_results: list[dict[str, Any]] = []

        try:
            if dsl is not None:
                def _on_step(result: dict[str, Any]) -> None:
                    nonlocal step_order
                    _persist_step(
                        db,
                        run=run,
                        order=step_order,
                        case=case,
                        result=result,
                    )
                    step_order += 1

                step_results = execute_ui_steps(
                    base_url=env.base_url,
                    steps=dsl,
                    ctx=ctx,
                    viewport=viewport,
                    headless=bool(run.headless),
                    browser_name=browser_name,
                    default_timeout_ms=default_timeout,
                    elements=elements,
                    screenshot_saver=_save_shot,
                    on_step=_on_step,
                    storage_state_path=storage_state_path,
                    extra_http_headers=browser_auth.extra_http_headers,
                    local_storage=browser_auth.local_storage,
                    screenshot_each_step=True,
                )
            else:
                _append_log(run, "script → running")
                db.commit()
                _log_commit_n = 0

                def _on_script_log(message: str) -> None:
                    nonlocal _log_commit_n
                    text = str(message or "").strip()
                    if not text:
                        return
                    _append_log(run, text)
                    _log_commit_n += 1
                    # Flush periodically so the UI can poll mid-script progress.
                    if _log_commit_n % 3 == 0:
                        db.commit()

                def _on_script_step(result: dict[str, Any]) -> None:
                    nonlocal step_order
                    _persist_step(
                        db,
                        run=run,
                        order=step_order,
                        case=case,
                        result=result,
                    )
                    step_order += 1

                step_results = execute_ui_script(
                    base_url=env.base_url,
                    script=script,
                    ctx=ctx,
                    viewport=viewport,
                    headless=bool(run.headless),
                    browser_name=browser_name,
                    default_timeout_ms=default_timeout,
                    screenshot_saver=_save_shot,
                    storage_state_path=storage_state_path,
                    extra_http_headers=browser_auth.extra_http_headers,
                    local_storage=browser_auth.local_storage,
                    on_log=_on_script_log,
                    on_step=_on_script_step,
                )
                if not step_results:
                    step_results = [
                        {
                            "action": "script",
                            "status": "error",
                            "error_message": "脚本无结果",
                            "selector": "",
                            "duration_ms": None,
                            "screenshot_key": None,
                            "detail_json": None,
                            "retry_count": 0,
                        }
                    ]
                    _on_script_step(step_results[0])
                last = step_results[-1]
                _append_log(run, f"script → {last.get('status') or 'error'}")
                db.commit()
        except Exception as ex:  # noqa: BLE001
            log.exception("ui run %s case %s crash", run_id, case.id)
            _persist_step(
                db,
                run=run,
                order=step_order,
                case=case,
                result={
                    "action": "",
                    "selector": "",
                    "status": "error",
                    "error_message": f"执行异常: {ex}",
                    "duration_ms": None,
                    "screenshot_key": None,
                    "detail_json": None,
                    "retry_count": 0,
                },
            )
            error += 1
            step_order += 1
            if stop_on_fail:
                _skip_rest(i + 1, "前序用例错误已跳过")
                break
            continue

        if not step_results:
            passed += 1
        for r in step_results:
            st = r.get("status") or "error"
            if st == "passed":
                pass
            elif st == "failed":
                case_failed = True
            elif st == "running":
                pass
            else:
                case_error = True

        if case_error:
            error += 1
        elif case_failed:
            failed += 1
        else:
            passed += 1

        _append_log(run, f"用例 #{case.id} 结束")
        db.commit()

        if (case_failed or case_error) and stop_on_fail and i < len(cases) - 1:
            _skip_rest(i + 1, "前序用例失败已跳过")
            break

    logs = []
    if isinstance(run.summary_json, dict) and isinstance(run.summary_json.get("log_lines"), list):
        logs = list(run.summary_json["log_lines"])
    summary = {
        "passed": passed,
        "failed": failed,
        "error": error,
        "skipped": skipped,
        "browser": browser_name,
        "account": run.account_name,
        "log_lines": logs,
    }
    if error:
        run.status = "error"
    elif failed:
        run.status = "failed"
    else:
        run.status = "passed"
    _append_log(run, f"执行结束 status={run.status}")
    summary["log_lines"] = (
        list(run.summary_json.get("log_lines") or [])
        if isinstance(run.summary_json, dict)
        else logs
    )
    run.summary_json = summary
    run.finished_at = _utc_now_iso()
    db.commit()
