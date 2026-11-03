"""Analyze a failed UI run and suggest a Playwright script patch (human confirm before write)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.errors import AppError
from app.models.functional.testcase import TestCase, TestCaseStep
from app.models.ui_automation.ui_automation import (
    UiCase,
    UiEnvironment,
    UiRun,
    UiRunStep,
    UiScenarioAgentSession,
)
from app.services.ai.effective_ai_settings import LlmRuntime, get_llm_runtime
from app.services.api_automation.api_scenario_events import get_event_bus
from app.services.api_automation.api_scenario_llm import llm_json
from app.services.ui_automation.ui_node_script import is_node_ui_script, is_runnable_ui_script
from app.services.ui_automation.ui_scenario_agent import _strip_code_fence, new_thread_id, resolve_env, stop_session
from app.user_messages import UI_CASE_NOT_FOUND

log = logging.getLogger(__name__)

_STORE: dict[str, dict[str, Any]] = {}


def new_fix_id() -> str:
    return "uifix-" + uuid.uuid4().hex


def get_fix(fix_id: str) -> dict[str, Any] | None:
    return _STORE.get(fix_id)


def put_fix(meta: dict[str, Any]) -> None:
    _STORE[str(meta["id"])] = meta


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


_LOCATOR_HINTS = (
    "getByRole",
    "getByText",
    "getByLabel",
    "getByPlaceholder",
    "getByTestId",
    "locator(",
)


def _normalize_suggested_script(raw: str, *, original: str, require_same_type: bool = True) -> str:
    code = _strip_code_fence(str(raw or "").strip())
    if not is_runnable_ui_script(code):
        raise ValueError("修订结果不是可执行脚本")
    if require_same_type and original and is_node_ui_script(original) != is_node_ui_script(code):
        raise ValueError("修订结果脚本类型与当前脚本不一致")
    if not code.endswith("\n"):
        code += "\n"
    return code


def _keeps_explore_locators(explored: str, suggested: str) -> bool:
    if not any(hint in explored for hint in _LOCATOR_HINTS):
        return True
    return any(hint in suggested for hint in _LOCATOR_HINTS)


def _session_events(session: UiScenarioAgentSession) -> list[dict[str, Any]]:
    db_events = session.events_json if isinstance(session.events_json, list) else []
    live = get_event_bus().snapshot(session.thread_id)
    return live if len(live) >= len(db_events) else list(db_events)


def _failed_step(db: Session, run: UiRun) -> UiRunStep | None:
    rows = (
        db.query(UiRunStep)
        .filter(UiRunStep.run_id == run.id, UiRunStep.project_id == run.project_id)
        .order_by(UiRunStep.order, UiRunStep.id)
        .all()
    )
    for row in rows:
        if row.status in ("failed", "error"):
            return row
    return rows[0] if rows else None


def resolve_fix_case(db: Session, run: UiRun, step: UiRunStep | None) -> UiCase:
    case_id = int(step.case_id) if step is not None and step.case_id else None
    if case_id is None and run.trigger_type == "case":
        case_id = int(run.target_id)
    if case_id is None:
        raise AppError("bad_request", "没有可修复的用例脚本", status_code=400)
    case = (
        db.query(UiCase)
        .filter(UiCase.id == case_id, UiCase.project_id == run.project_id)
        .first()
    )
    if case is None:
        raise AppError("not_found", UI_CASE_NOT_FOUND, status_code=404)
    if not is_runnable_ui_script(case.script or ""):
        raise AppError("bad_request", "没有可修复的用例脚本", status_code=400)
    return case


def _manual_case_context(db: Session, case: UiCase) -> dict[str, Any]:
    if not case.manual_case_id:
        return {}
    tc = (
        db.query(TestCase)
        .filter(TestCase.id == case.manual_case_id, TestCase.project_id == case.project_id)
        .first()
    )
    if tc is None:
        return {}
    steps = (
        db.query(TestCaseStep)
        .filter(TestCaseStep.case_id == tc.id)
        .order_by(TestCaseStep.order, TestCaseStep.id)
        .all()
    )
    from app.services.functional.test_data_vars import as_str_dict, case_test_data_text

    return {
        "title": tc.title,
        "precondition": tc.precondition or "",
        "test_data": case_test_data_text(variables=tc.variables_json, test_data=tc.test_data),
        "variables": as_str_dict(tc.variables_json),
        "overall_expected": tc.overall_expected or "",
        "steps": [{"action": s.action, "expected": s.expected} for s in steps],
    }


def _session_for_run(db: Session, run: UiRun) -> UiScenarioAgentSession | None:
    found = (
        db.query(UiScenarioAgentSession)
        .filter(
            UiScenarioAgentSession.project_id == run.project_id,
            UiScenarioAgentSession.run_id == run.id,
        )
        .first()
    )
    if found is not None:
        return found
    case_id = run.target_id if run.trigger_type == "case" else None
    if case_id is None:
        return None
    return (
        db.query(UiScenarioAgentSession)
        .filter(
            UiScenarioAgentSession.project_id == run.project_id,
            UiScenarioAgentSession.saved_ui_case_id == case_id,
        )
        .order_by(UiScenarioAgentSession.id.desc())
        .first()
    )


def build_fix_context(db: Session, run: UiRun, step: UiRunStep, case: UiCase) -> dict[str, Any]:
    session = _session_for_run(db, run)
    plan = session.plan_json if session is not None and isinstance(session.plan_json, dict) else {}
    report = plan.get("explore_report") if isinstance(plan.get("explore_report"), dict) else {}
    last_url = ""
    if isinstance(report, dict):
        last_url = str(report.get("last_url") or report.get("url") or "")
        if not last_url:
            steps = report.get("steps")
            if isinstance(steps, list) and steps:
                last = steps[-1]
                if isinstance(last, dict):
                    last_url = str(last.get("page_url") or last.get("url") or "")
    env = None
    if run.env_id is not None:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.id == run.env_id, UiEnvironment.project_id == run.project_id)
            .first()
        )
    has_storage = bool(env and (env.auth_states_encrypted or "").strip())
    return {
        "run_id": run.id,
        "run_status": run.status,
        "error_message": (step.error_message or "")[:4000],
        "step_status": step.status,
        "step_action": step.action or "",
        "current_script": (case.script or "")[:12000],
        "manual_case": _manual_case_context(db, case),
        "explore": {
            "last_url": last_url[:500],
            "auth_mode": str(plan.get("_auth_mode") or ""),
            "account_name": str(plan.get("_account_name") or run.account_name or ""),
            "goal": str(plan.get("goal") or ""),
            "start_path": str(plan.get("start_path") or ""),
        },
        "env": {
            "has_storage_state": has_storage,
            "token_inject": getattr(env, "token_inject", None) or "none",
            "account_name": run.account_name or "",
        },
    }


def _repair_prompt(step: UiRunStep, case: UiCase) -> str:
    error = (step.error_message or "").strip()[:1500]
    script = (case.script or "").strip()[:4000]
    return (
        "试跑失败，请重新探索当前流程并基于真实页面生成可执行脚本，不要凭空改脚本。\n"
        f"失败信息：{error or '（无详细报错）'}\n"
        f"当前脚本：\n{script}"
    )


def _empty_fix_meta(
    *,
    fix_id: str,
    run: UiRun,
    case: UiCase,
    explore_session_id: int | None,
    status: str,
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "id": fix_id,
        "run_id": run.id,
        "case_id": case.id,
        "project_id": run.project_id,
        "status": status,
        "plan": None,
        "current_script": case.script or "",
        "error_message": error_message,
        "patch_message": "",
        "rerun_id": None,
        "explore_session_id": explore_session_id,
        "events": [],
    }


def start_repair_explore(
    db: Session,
    run: UiRun,
    *,
    user_id: int,
    llm_cfg: LlmRuntime,
) -> dict[str, Any]:
    _ = llm_cfg
    if run.status not in ("failed", "error"):
        raise AppError("bad_request", "仅支持对失败/错误执行记录做修复", status_code=400)
    step = _failed_step(db, run)
    if step is None:
        raise AppError("bad_request", "执行记录没有失败步骤", status_code=400)
    case = resolve_fix_case(db, run, step)
    origin = _session_for_run(db, run)
    origin_plan = origin.plan_json if origin is not None and isinstance(origin.plan_json, dict) else {}
    raw_auth = origin_plan.get("_auth_mode")
    if raw_auth in ("none", "account", "token"):
        auth_mode: str | None = str(raw_auth)
    elif run.account_name:
        auth_mode = "account"
    else:
        # Preserve follow-env (None) so repair reuses env token_inject
        auth_mode = None
    account_name = (
        str(origin_plan.get("_account_name") or run.account_name or "").strip() or None
        if auth_mode == "account"
        else None
    )
    testcase_id = origin.testcase_id if origin is not None else case.manual_case_id
    creds = origin.credentials_json if origin is not None and isinstance(origin.credentials_json, dict) else None
    env = resolve_env(db, run.project_id, run.env_id or (origin.env_id if origin is not None else None))
    prompt = _repair_prompt(step, case)
    fix_id = new_fix_id()
    thread_id = new_thread_id()
    get_event_bus().replace(thread_id, [])
    repair_plan: dict[str, Any] = {
        "_max_steps": 25,
        "_headed": True,
        "engine": "playwright",
        "_auth_mode": auth_mode,
        "_account_name": account_name,
        "_repair_run_id": run.id,
        "_repair_case_id": case.id,
        "_repair_fix_id": fix_id,
        "_single_case": True,
    }
    for key in ("goal", "start_path", "checklist", "case_name"):
        if origin_plan.get(key):
            repair_plan[key] = origin_plan[key]
    row = UiScenarioAgentSession(
        project_id=run.project_id,
        user_id=user_id,
        thread_id=thread_id,
        status="running",
        prompt=prompt,
        testcase_id=testcase_id,
        env_id=env.id,
        browser=run.browser or env.browser or "chromium",
        messages_json=[{"role": "user", "content": prompt}],
        plan_json=repair_plan,
        credentials_json=creds,
        events_json=[],
        playwright_code="",
        dry_run_error="",
        error_message="",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    meta = _empty_fix_meta(
        fix_id=fix_id,
        run=run,
        case=case,
        explore_session_id=row.id,
        status="exploring",
    )
    put_fix(meta)

    from app.api.ui_automation.ui_scenario_agent import _start_explore

    _start_explore(db, row, env.queue_name or "ui")
    db.refresh(row)
    return hydrate_fix(db, meta)


def hydrate_fix(db: Session, meta: dict[str, Any]) -> dict[str, Any]:
    sid = meta.get("explore_session_id")
    if not sid:
        return meta
    session = db.get(UiScenarioAgentSession, int(sid))
    if session is None:
        return meta
    meta["events"] = _session_events(session)
    if meta.get("status") == "exploring":
        complete_repair_from_explore(db, session)
        fresh = get_fix(str(meta["id"]))
        if fresh is not None:
            fresh["events"] = _session_events(session)
            put_fix(fresh)
            return fresh
    put_fix(meta)
    return meta


def complete_repair_from_explore(db: Session, session: UiScenarioAgentSession) -> dict[str, Any] | None:
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    fix_id = str(plan.get("_repair_fix_id") or "")
    if not fix_id:
        return None
    meta = get_fix(fix_id)
    if meta is None or meta.get("status") != "exploring":
        return meta
    meta["events"] = _session_events(session)
    meta["explore_session_id"] = session.id

    if session.status in ("running", "awaiting_human", "pending"):
        put_fix(meta)
        return meta
    if session.status == "cancelled":
        meta["status"] = "cancelled"
        meta["patch_message"] = "已取消"
        meta["error_message"] = session.error_message or ""
        put_fix(meta)
        return meta
    if session.status == "error":
        meta["status"] = "error"
        meta["error_message"] = session.error_message or "重新探索失败"
        put_fix(meta)
        return meta

    explored = session.playwright_code or ""
    if not is_runnable_ui_script(explored):
        meta["status"] = "error"
        meta["error_message"] = session.error_message or "重新探索未产生可执行脚本"
        put_fix(meta)
        return meta
    explored = _normalize_suggested_script(explored, original=explored, require_same_type=False)
    suggestion = _suggest_from_explore(db, meta, session, explored)
    meta["plan"] = suggestion
    meta["status"] = "awaiting_confirm"
    meta["error_message"] = ""
    session.status = "done"
    session.playwright_code = suggestion["suggested_script"]
    db.commit()
    put_fix(meta)
    return meta


def _suggest_from_explore(
    db: Session,
    meta: dict[str, Any],
    session: UiScenarioAgentSession,
    explored: str,
) -> dict[str, Any]:
    fallback = {
        "summary": "已根据重新探索的页面生成脚本，请确认后写入原用例。",
        "likely_causes": [],
        "changes": ["用本次探索得到的 locator 与等待替换当前脚本"],
        "warnings": [],
        "suggested_script": explored,
    }
    run = db.get(UiRun, int(meta["run_id"]))
    case = db.get(UiCase, int(meta["case_id"])) if meta.get("case_id") else None
    if run is None or case is None:
        return fallback
    step = _failed_step(db, run)
    ctx = build_fix_context(db, run, step, case) if step is not None else {}
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    ctx["explored_script"] = explored[:12000]
    ctx["explore_report"] = plan.get("explore_report") if isinstance(plan.get("explore_report"), dict) else {}
    node = is_node_ui_script(explored)
    script_kind = (
        "Node.js Playwright（必须定义 async function run(page, agent)，不要写 import）"
        if node
        else "Python Playwright 同步脚本（必须定义 def run(page)，不要写 import）"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是 UI 自动化失败修复助手。已经重新探索了真实页面。"
                "suggested_script 必须以这次探索编译出的脚本为准，最多微调断言以贴合功能用例期望，"
                "不得丢掉探索得到的 locator，不得凭报错编 locator。"
                "输出 JSON："
                '{"summary":"...","likely_causes":["..."],"changes":["..."],'
                '"warnings":["..."],"suggested_script":"完整可运行脚本"}。'
                f"suggested_script 必须是{script_kind}。"
                "page.goto 的参数必须是以 / 开头的相对路径。"
                "修脚本解决不了的环境/账号问题放入 warnings。"
                "只输出 JSON，不要 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": "失败上下文与探索结果：\n" + json.dumps(ctx, ensure_ascii=False),
        },
    ]
    try:
        cfg = get_llm_runtime(db)
        if not cfg.api_key.strip():
            return fallback
        raw_plan = llm_json(messages, cfg=cfg, scene="UI 失败修复")
    except Exception:  # noqa: BLE001
        log.exception("ui run fix suggest after explore failed")
        return fallback
    suggested = explored
    raw_script = str(raw_plan.get("suggested_script") or "")
    if raw_script.strip():
        try:
            candidate = _normalize_suggested_script(raw_script, original=explored)
            if _keeps_explore_locators(explored, candidate):
                suggested = candidate
        except ValueError:
            suggested = explored
    return {
        "summary": str(raw_plan.get("summary") or fallback["summary"]).strip() or fallback["summary"],
        "likely_causes": _string_list(raw_plan.get("likely_causes")),
        "changes": _string_list(raw_plan.get("changes")) or fallback["changes"],
        "warnings": _string_list(raw_plan.get("warnings")),
        "suggested_script": suggested,
    }


def _sync_session_script(db: Session, run: UiRun, old_script: str, new_script: str) -> None:
    session = _session_for_run(db, run)
    if session is None:
        return
    session.playwright_code = new_script
    if not isinstance(session.plan_json, dict):
        return
    plan = dict(session.plan_json)
    scripts = plan.get("case_scripts")
    if not isinstance(scripts, list):
        return
    updated = False
    for item in scripts:
        if isinstance(item, dict) and str(item.get("code") or "") == old_script:
            item["code"] = new_script
            updated = True
            break
    if not updated:
        for item in scripts:
            if isinstance(item, dict) and item.get("ready"):
                item["code"] = new_script
                break
    plan["case_scripts"] = scripts
    session.plan_json = plan
    flag_modified(session, "plan_json")


def confirm_fix(
    db: Session,
    meta: dict[str, Any],
    *,
    action: str,
    apply_patch: bool,
    rerun: bool,
    user_id: int | None,
) -> dict[str, Any]:
    if action == "cancel":
        sid = meta.get("explore_session_id")
        if sid and meta.get("status") == "exploring":
            session = db.get(UiScenarioAgentSession, int(sid))
            if session is not None and session.status in ("running", "awaiting_human"):
                try:
                    stop_session(db, session)
                except AppError:
                    pass
        meta["status"] = "cancelled"
        meta["patch_message"] = "已取消"
        put_fix(meta)
        return meta

    if not apply_patch:
        meta["status"] = "confirmed"
        meta["patch_message"] = "已确认，未修改用例。勾选「确认时写入修补」后再点确认，才会把建议写回去。"
        put_fix(meta)
        return meta

    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
    suggested = str(plan.get("suggested_script") or "").strip()
    case_id = meta.get("case_id")
    case = db.query(UiCase).filter(UiCase.id == case_id).first() if case_id else None
    if case is None:
        meta["status"] = "error"
        meta["error_message"] = UI_CASE_NOT_FOUND
        put_fix(meta)
        return meta
    try:
        new_script = _normalize_suggested_script(suggested, original=case.script or "")
    except ValueError as e:
        meta["status"] = "error"
        meta["error_message"] = str(e)
        put_fix(meta)
        return meta

    old_script = case.script or ""
    case.script = new_script
    run = db.get(UiRun, int(meta["run_id"]))
    if run is not None:
        _sync_session_script(db, run, old_script, new_script)
    db.commit()
    meta["status"] = "confirmed"
    meta["patch_message"] = f"已写入 UI 用例 #{case.id} 脚本。"
    meta["current_script"] = new_script

    if rerun and run is not None:
        from app.tasks.ui_automation.run_ui_case import enqueue_run_ui_case

        env = None
        if run.env_id is not None:
            env = db.get(UiEnvironment, run.env_id)
        queue = (env.queue_name if env else run.queue_name) or "ui"
        new_run = UiRun(
            project_id=run.project_id,
            trigger_type="case",
            target_id=case.id,
            env_id=run.env_id,
            browser=run.browser or "chromium",
            headless=bool(run.headless),
            account_name=run.account_name,
            queue_name=queue,
            status="pending",
            triggered_by=user_id or run.triggered_by,
        )
        db.add(new_run)
        db.commit()
        db.refresh(new_run)
        enqueue_run_ui_case(new_run.id, queue=queue)
        session = _session_for_run(db, run)
        if session is not None:
            session.run_id = new_run.id
            db.commit()
        meta["rerun_id"] = new_run.id
        meta["patch_message"] = f"已写入 UI 用例 #{case.id} 脚本，并触发试跑 #{new_run.id}。"

    put_fix(meta)
    return meta
