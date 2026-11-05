"""Conversational UI scenario agent: plan → explore → revise → confirm UiCase."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Callable

from sqlalchemy.orm import Session, joinedload

from app.errors import AppError
from app.models.functional.testcase import TestCase
from app.models.ui_automation.ui_automation import UiCase, UiEnvironment, UiScenarioAgentSession
from app.services.api_automation.api_scenario_events import emit_log, get_event_bus
from app.services.ai.effective_ai_settings import LlmRuntime, get_llm_runtime, get_midscene_runtime
from app.services.ai import llm
from app.services.functional.testcase_run_finalize import (
    execution_mode_of,
    finalize_agent_once,
    judge_agent_once,
)
from app.services.ui_automation.ui_ai_explorer import (
    DEFAULT_MAX_STEPS,
    _extract_json,
    build_plan_from_testcase,
    run_explore_loop,
    steps_to_script,
    trace_to_explore_script,
    trace_to_ui_steps,
)
from app.services.ui_automation.ui_explore_engines import (
    run_midscene_engine,
    run_playwright_cli_engine,
)
from app.services.ui_automation.ui_explore_report import (
    annotate_steps,
    build_explore_report,
    ensure_plan_matrix,
)
from app.services.ui_automation.ui_node_script import (
    is_node_ui_script,
    is_runnable_ui_script,
)
from app.services.ui_automation.ui_script_review import review_and_fix_case_scripts
from app.services.ui_automation.ui_env_auth import (
    BrowserAuth,
    env_has_inject_config,
    normalize_auth_mode,
    resolve_browser_auth,
)
from app.services.functional.test_data_vars import as_str_dict, case_test_data_text
from app.services.ui_automation.ui_run_executor import _build_ctx, _load_env
from app.user_messages import (
    TEST_CASE_NOT_FOUND,
    UI_ENV_BASE_URL_REQUIRED,
    UI_ENV_NOT_FOUND,
    UI_ENV_TOKEN_REQUIRED,
)

log = logging.getLogger(__name__)

MANUAL_STOP_MESSAGE = "已手动停止探索"

_COMBO_CREDS = [
    # 账号密码：admin admin123  /  账号密码 admin admin123
    re.compile(
        r"(?:账号密码|用户名密码|username\s*/?\s*password|user\s*/?\s*pass(?:word)?)"
        r"\s*[:=：]?\s*([^\s,，；;]+)\s+([^\s,，；;]+)",
        re.I,
    ),
    # 用户名 admin 与密码 admin123 / 输入用户名 admin 和密码 admin123
    re.compile(
        r"(?:账号|用户名|username|user(?:name)?)\s*[:=：]?\s*([^\s,，；;与和]+)"
        r"\s*(?:与|和|/|,|，)\s*(?:密码|password|passwd|pwd)\s*[:=：]?\s*([^\s,，；;]+)",
        re.I,
    ),
]
_USER_PATTERNS = [
    re.compile(r"(?:账号|用户名|username|user(?:name)?)\s*[:=：]\s*([^\s,，；;]+)", re.I),
    re.compile(r"(?:账号|用户名)\s+([^\s,，；;与和]+)", re.I),
]
_PASS_PATTERNS = [
    re.compile(r"(?:密码|password|passwd|pwd)\s*[:=：]\s*([^\s,，；;]+)", re.I),
    re.compile(r"(?:密码)\s+([^\s,，；;]+)", re.I),
]
_REEXPLORE_HINT = re.compile(r"重新探索|重跑探索|再探索|重新生成步骤|re-?explore", re.I)
# Outcome / step-index tokens must never become username/password via regex scrape.
_BAD_CRED_VALUE = re.compile(
    r"^(?:"
    r"登录成功|登录失败|成功|失败|通过|离开|正确|错误|无效|"
    r"密码|账号|用户名|username|password|passwd|pwd|"
    r"\d+\.?"
    r")$",
    re.I,
)


def new_thread_id() -> str:
    return uuid.uuid4().hex


def plan_auth_mode(plan: dict[str, Any] | None) -> str | None:
    """Return explicit plan auth_mode, or None to follow env inject."""
    if not isinstance(plan, dict):
        return None
    return normalize_auth_mode(str(plan.get("_auth_mode") or "") or None)


def resolve_plan_browser_auth(env: Any, plan: dict[str, Any] | None) -> BrowserAuth:
    """Honor plan ``_auth_mode=none`` (login); otherwise follow env inject KV."""
    mode = plan_auth_mode(plan)
    if mode == "none":
        return resolve_browser_auth(env, auth_mode="none")
    if mode in ("account", "token"):
        name = None
        if isinstance(plan, dict):
            raw = str(plan.get("_account_name") or "").strip()
            name = raw or None
        return resolve_browser_auth(env, auth_mode=mode, account_name=name)
    return resolve_browser_auth(env, apply_env_defaults=True)


def is_plausible_credential_value(value: str, *, kind: str = "") -> bool:
    """True when a scraped/bound credential token looks usable (not outcome copy)."""
    text = str(value or "").strip().strip("\"'")
    if not text or len(text) > 64:
        return False
    if _BAD_CRED_VALUE.match(text):
        return False
    if kind == "username" and re.match(r"^密码", text):
        return False
    return True


def extract_credentials_from_text(text: str) -> dict[str, str]:
    """Pull username/password from free-form chat/step text. Never logs secrets.

    Rejects outcome phrases (e.g. 登录成功) and step indexes so case titles
    like 「用户名密码登录成功」+「1. …」 cannot poison ctx.
    """
    out: dict[str, str] = {}
    raw = text or ""
    for pat in _COMBO_CREDS:
        for m in pat.finditer(raw):
            user = m.group(1).strip().strip("\"'")
            pwd = m.group(2).strip().strip("\"'")
            if is_plausible_credential_value(user, kind="username") and is_plausible_credential_value(
                pwd, kind="password"
            ):
                return {"username": user, "password": pwd}
    for pat in _USER_PATTERNS:
        for m in pat.finditer(raw):
            user = m.group(1).strip().strip("\"'")
            if is_plausible_credential_value(user, kind="username"):
                out["username"] = user
                break
        if out.get("username"):
            break
    for pat in _PASS_PATTERNS:
        for m in pat.finditer(raw):
            pwd = m.group(1).strip().strip("\"'")
            if is_plausible_credential_value(pwd, kind="password"):
                out["password"] = pwd
                break
        if out.get("password"):
            break
    return out


def merge_credentials(*parts: dict[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for p in parts:
        if not p:
            continue
        for k, v in p.items():
            if v:
                merged[str(k)] = str(v)
    return merged


def credentials_to_ctx(creds: dict[str, str]) -> dict[str, str]:
    """Map credentials into script env. Always include username/password as strings."""
    ctx: dict[str, str] = {}
    user = str(creds.get("username") or creds.get("user") or "")
    pwd = str(creds.get("password") or creds.get("passwd") or "")
    ctx["username"] = user
    ctx["user"] = user
    ctx["LOGIN_USERNAME"] = user
    ctx["password"] = pwd
    ctx["passwd"] = pwd
    ctx["LOGIN_PASSWORD"] = pwd
    for k, v in creds.items():
        if k not in ctx and v is not None:
            ctx[str(k)] = str(v)
    return ctx


def script_env_from_ctx(ctx: dict[str, str] | None) -> dict[str, str]:
    """Env dict for Node dry-run: always string username/password so fill() never gets undefined."""
    base = {str(k): str(v) for k, v in (ctx or {}).items() if v is not None}
    base.setdefault("username", "")
    base.setdefault("password", "")
    base.setdefault("user", base["username"])
    base.setdefault("passwd", base["password"])
    base.setdefault("LOGIN_USERNAME", base["username"])
    base.setdefault("LOGIN_PASSWORD", base["password"])
    return base


def _append_message(session: UiScenarioAgentSession, role: str, content: str) -> None:
    msgs = list(session.messages_json) if isinstance(session.messages_json, list) else []
    msgs.append({"role": role, "content": content})
    session.messages_json = msgs


def _persist_event(session: UiScenarioAgentSession, ev: dict[str, Any]) -> None:
    events = list(session.events_json) if isinstance(session.events_json, list) else []
    events.append(ev)
    session.events_json = events


def _emit(
    session: UiScenarioAgentSession,
    *,
    node: str,
    message: str,
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> None:
    def persist(ev: dict[str, Any]) -> None:
        _persist_event(session, ev)

    emit_log(session.thread_id, node=node, message=message, level=level, data=data, persist=persist)


def _emit_commit(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    node: str,
    message: str,
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> None:
    _emit(session, node=node, message=message, level=level, data=data)
    db.commit()


def session_headed(session: UiScenarioAgentSession) -> bool:
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    return bool(plan.get("_headed"))


def _attach_explore_report(
    session: UiScenarioAgentSession,
    *,
    plan: dict[str, Any],
    steps: list[dict[str, Any]] | None,
    script: str = "",
    engine: str = "",
) -> dict[str, Any]:
    """Annotate steps, build explore report, persist into plan_json."""
    case_scripts = plan.get("case_scripts") if isinstance(plan.get("case_scripts"), list) else []
    bound_matrix = plan.get("matrix") if isinstance(plan.get("matrix"), list) else None
    plan = ensure_plan_matrix(plan)
    if bound_matrix:
        plan["matrix"] = bound_matrix
    if case_scripts:
        plan["case_scripts"] = case_scripts
    # Prefer matrix already bound by explore engine (structured steps + ready flags).
    annotated = annotate_steps(steps, matrix=plan.get("matrix") if isinstance(plan.get("matrix"), list) else [])
    report = build_explore_report(plan=plan, steps=annotated, script=script, engine=engine)
    if isinstance(plan.get("matrix"), list) and plan["matrix"]:
        report["matrix"] = plan["matrix"]
    plan["explore_report"] = report
    plan["matrix"] = report.get("matrix") or plan.get("matrix")
    session.plan_json = plan
    session.result_steps_json = annotated
    return report


def _emit_matrix_summary(db: Session, session: UiScenarioAgentSession, plan: dict[str, Any]) -> None:
    matrix = plan.get("matrix") if isinstance(plan.get("matrix"), list) else []
    if not matrix:
        return
    lines = [f"{r.get('id')}: {r.get('name')}" for r in matrix if isinstance(r, dict)]
    preview = "；".join(lines[:8])
    if len(lines) > 8:
        preview += f"…（共 {len(lines)} 条）"
    _emit_commit(db, session, node="plan", message=f"用例矩阵（{len(lines)}）：{preview}")
    get_event_bus().append(session.thread_id, {"type": "matrix", "matrix": matrix})


def build_plan_from_prompt(
    *,
    prompt: str,
    base_url: str,
    llm_cfg: LlmRuntime,
    model: str | None = None,
) -> dict[str, Any]:
    """Plan for explore-first: goal + checklist only; matrix filled after explore."""
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Web UI 自动化探索规划助手。根据用户自然语言写出探索目标与检查点。"
                "只输出 JSON 对象，键："
                "goal(string 一次探索要覆盖的业务目标), "
                "start_path(string, 以/开头的相对路径), "
                "checklist(array of string 探索时要覆盖的功能点，按顺序), "
                "case_name(string 建议场景名)。"
                "不要输出 matrix / 用例矩阵；用例脚本在探索完成后再生成。"
                "checklist 应覆盖用户描述的相关功能（如登录、列表、新建），便于一次探索走完。"
                "禁止一次输出多个 JSON 或数组；只输出一个 JSON 对象。"
            ),
        },
        {
            "role": "user",
            "content": f"被测 base_url: {base_url}\n用户需求:\n{prompt or '(无描述，打开首页并做基本冒烟)'}\n",
        },
    ]
    raw = llm.chat_complete(messages, model=model, db=None, cfg=llm_cfg, scene="UI 场景规划")
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("计划不是 JSON 对象")
    start = str(data.get("start_path") or "/").strip() or "/"
    if not start.startswith("/"):
        start = "/" + start
    checklist = data.get("checklist") if isinstance(data.get("checklist"), list) else []
    return {
        "goal": str(data.get("goal") or prompt or "UI 探索"),
        "start_path": start,
        "checklist": [str(x) for x in checklist][:20],
        "case_name": str(data.get("case_name") or "").strip() or None,
        "matrix": [],
        "case_scripts": [],
    }


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def revise_script_with_llm(
    *,
    script: str,
    instruction: str,
    plan: dict[str, Any] | None,
    llm_cfg: LlmRuntime,
    model: str | None = None,
) -> str:
    node = is_node_ui_script(script)
    if node:
        system = (
            "你是 UI 自动化脚本编辑助手。根据用户指令修改 Node.js Playwright 脚本。"
            "脚本必须定义 async function run(page, agent)。不要写 import。"
            "可使用 page、agent、env。page 是 Playwright，agent 是 Midscene PlaywrightAgent。"
            "page.goto 的参数必须是以 / 开头的相对路径。"
            "验证码：用 agent.aiQuery 读图中的字符，再用 page.getByPlaceholder/getByRole/.locator().fill 填入；"
            "不要用 aiInput 操作验证码，不要把验证码字符写死。"
            "只输出 JavaScript，不要 Markdown。"
        )
    else:
        system = (
            "你是 UI 自动化脚本编辑助手。根据用户指令修改 Python Playwright 同步脚本。"
            "脚本必须定义 def run(page):，不要写 import。"
            "可使用已注入的 page、expect、env、re。"
            "page.goto 的参数必须是以 / 开头的相对路径。"
            "只输出 Python 代码，不要 Markdown。"
        )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "plan": plan or {},
                    "current_script": script,
                    "instruction": instruction,
                },
                ensure_ascii=False,
            ),
        },
    ]
    raw = llm.chat_complete(
        messages,
        model=model,
        db=None,
        cfg=llm_cfg,
        json_mode=False,
        scene="UI 场景修订",
    )
    code = _strip_code_fence(raw)
    if node:
        if "async function run" not in code:
            raise ValueError("修订结果不是可执行脚本")
    elif "def run" not in code:
        raise ValueError("修订结果不是可执行脚本")
    if not code.endswith("\n"):
        code += "\n"
    return code


def resolve_env(db: Session, project_id: int, env_id: int | None) -> UiEnvironment:
    env = _load_env(db, project_id, env_id)
    if env is None:
        raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
    return env


def _explore_with_engine(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    engine: str,
    env: UiEnvironment,
    plan: dict[str, Any],
    ctx: dict[str, str],
    llm_cfg: LlmRuntime,
    base_url: str,
    browser_name: str,
    headed: bool,
    timeout_ms: int,
    max_steps: int,
    title: str,
    browser_factory: Callable[[], Any] | None,
    browser_auth: BrowserAuth | None = None,
) -> None:
    del title, browser_factory
    username = ctx.get("username") or ""
    password = ctx.get("password") or ""
    auth = browser_auth or BrowserAuth()
    vp = env.viewport_json if isinstance(env.viewport_json, dict) else {"width": 1280, "height": 720}
    viewport = {"width": int(vp.get("width") or 1280), "height": int(vp.get("height") or 720)}
    def _engine_log(message: str, data: dict[str, Any] | None = None) -> None:
        _emit_commit(db, session, node="explore", message=str(message or "")[:2000], data=data)

    def _on_screenshot(png: bytes, meta: dict[str, Any]) -> str | None:
        from app.services.file_storage import save_ui_explore_screenshot

        n = int(meta.get("step") or 0)
        action = str(meta.get("action") or "step").strip() or "step"
        fname = f"{n:03d}_{action}.png"
        try:
            key, _ = save_ui_explore_screenshot(session.project_id, session.id, fname, png)
        except Exception as ex:  # noqa: BLE001
            _engine_log(f"截图保存失败: {ex}")
            return None
        _engine_log(f"已截图 step={n} action={action}", data={"screenshot_key": key})
        return key

    session_id = session.id
    human_wait_sec = 15 * 60

    def _should_cancel() -> bool:
        from app.deps import SessionLocal

        sdb = SessionLocal()
        try:
            row = sdb.get(UiScenarioAgentSession, session_id)
            if row is None:
                return True
            return row.status not in ("running", "awaiting_human")
        finally:
            sdb.close()

    def _wait_human_reply(reason: str) -> str | None:
        from app.deps import SessionLocal

        hint = (reason or "需要人工协助（例如验证码）").strip()[:500]
        sdb = SessionLocal()
        try:
            row = sdb.get(UiScenarioAgentSession, session_id)
            if row is None:
                return None
            if row.status not in ("running", "awaiting_human"):
                return None
            plan_obj = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
            plan_obj.pop("_human_reply", None)
            row.plan_json = plan_obj
            row.status = "awaiting_human"
            row.error_message = ""
            _emit(row, node="explore", message=hint, level="warn")
            get_event_bus().append(
                row.thread_id,
                {"type": "need_human", "message": hint, "node": "explore"},
            )
            _append_message(
                row,
                "assistant",
                f"{hint}\n浏览器保持打开。请在页面上手动操作，或在此输入验证码/说明后发送；也可发送「继续」。",
            )
            sdb.commit()
        finally:
            sdb.close()

        deadline = time.monotonic() + human_wait_sec
        while time.monotonic() < deadline:
            if _should_cancel():
                return None
            sdb = SessionLocal()
            try:
                row = sdb.get(UiScenarioAgentSession, session_id)
                if row is None:
                    return None
                if row.status == "error":
                    return None
                plan_obj = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
                if "_human_reply" in plan_obj:
                    reply = str(plan_obj.pop("_human_reply") or "")
                    row.plan_json = plan_obj
                    row.status = "running"
                    _emit(row, node="explore", message="已收到人工协助，继续探索…")
                    sdb.commit()
                    return reply
            finally:
                sdb.close()
            time.sleep(0.5)
        return None

    try:
        if engine == "playwright":
            if _should_cancel():
                raise RuntimeError(MANUAL_STOP_MESSAGE)
            trace, code = run_playwright_cli_engine(
                base_url=base_url,
                start_path=str(plan.get("start_path") or "/"),
                goal=str(plan.get("goal") or session.prompt or ""),
                username=username,
                password=password,
                ctx=ctx,
                llm=llm_cfg,
                headed=headed,
                timeout_ms=timeout_ms,
                max_steps=max_steps,
                plan=plan,
                on_log=_engine_log,
                should_cancel=_should_cancel,
                on_need_human=_wait_human_reply if headed else None,
                on_screenshot=_on_screenshot,
                storage_state=auth.storage_state,
                extra_http_headers=auth.extra_http_headers,
                local_storage=auth.local_storage,
                skip_login=auth.skip_login,
            )
        else:
            # midscene label: same CLI explore; Midscene only in generated complex steps
            midscene = get_midscene_runtime(db)
            if _should_cancel():
                raise RuntimeError(MANUAL_STOP_MESSAGE)
            trace, code = run_midscene_engine(
                base_url=base_url,
                start_path=str(plan.get("start_path") or "/"),
                goal=str(plan.get("goal") or session.prompt or ""),
                username=username,
                password=password,
                ctx=ctx,
                midscene=midscene,
                browser=browser_name,
                headed=headed,
                viewport=viewport,
                timeout_ms=timeout_ms,
                on_log=_engine_log,
                should_cancel=_should_cancel,
                on_need_human=_wait_human_reply if headed else None,
                on_screenshot=_on_screenshot,
                llm=llm_cfg,
                max_steps=max_steps,
                plan=plan,
                prefer_cli=True,
                storage_state=auth.storage_state,
                extra_http_headers=auth.extra_http_headers,
                local_storage=auth.local_storage,
                skip_login=auth.skip_login,
            )
    except AppError as ex:
        session.status = "error"
        session.error_message = ex.message
        _emit_commit(db, session, node="error", message=ex.message, level="error")
        get_event_bus().append(session.thread_id, {"type": "error", "message": ex.message})
        _finalize_agent_once_on_failure(db, session, status="error", error_message=ex.message)
        db.commit()
        return
    except Exception as ex:  # noqa: BLE001
        msg = str(ex)
        stopped = MANUAL_STOP_MESSAGE in msg
        if not stopped:
            log.exception("ui engine explore failed id=%s engine=%s", session.id, engine)
        db.refresh(session)
        if session.status != "error":
            session.status = "error"
        session.error_message = MANUAL_STOP_MESSAGE if stopped else msg
        _emit_commit(
            db,
            session,
            node="error",
            message=session.error_message,
            level="warn" if stopped else "error",
        )
        get_event_bus().append(session.thread_id, {"type": "error", "message": session.error_message})
        _finalize_agent_once_on_failure(
            db,
            session,
            status="cancelled" if stopped else "error",
            error_message=session.error_message,
        )
        db.commit()
        return

    # Human-assist path mutates the row via a separate SessionLocal; reload before write-back.
    db.refresh(session)
    # Stop may have landed while the engine was finishing / codegen — do not overwrite.
    if session.status not in ("running", "awaiting_human") or _should_cancel():
        if session.status != "error":
            session.status = "error"
            session.error_message = MANUAL_STOP_MESSAGE
            _emit_commit(
                db,
                session,
                node="explore",
                message=MANUAL_STOP_MESSAGE,
                level="warn",
            )
            get_event_bus().append(
                session.thread_id, {"type": "error", "message": MANUAL_STOP_MESSAGE}
            )
            _finalize_agent_once_on_failure(
                db, session, status="cancelled", error_message=MANUAL_STOP_MESSAGE
            )
            db.commit()
        return
    # Drop any captured storageState — auth is env inject KV, not per-account storage.
    if isinstance(plan, dict):
        plan.pop("_captured_storage_state", None)
    if isinstance(session.plan_json, dict):
        session.plan_json.pop("_captured_storage_state", None)
    _complete_explore(
        db,
        session,
        plan=plan,
        trace=trace,
        code=code,
        engine=engine,
        script_kind="node",
    )


def _screenshot_keys_from_events(session: UiScenarioAgentSession) -> list[str]:
    events = session.events_json if isinstance(session.events_json, list) else []
    keys: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        key = str(data.get("screenshot_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _finalize_agent_once_on_failure(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    status: str,
    error_message: str,
) -> None:
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    if execution_mode_of(plan) != "agent_once":
        return
    run = finalize_agent_once(
        db,
        session,
        status=status,
        error_message=error_message,
        summary=error_message or f"Agent 直跑{status}",
        actual_result=error_message,
        screenshot_keys=_screenshot_keys_from_events(session),
    )
    if run is not None:
        get_event_bus().append(
            session.thread_id,
            {
                "type": "done",
                "status": status,
                "case_run_id": run.id,
                "case_run_status": run.status,
            },
        )


def _complete_explore(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    plan: dict[str, Any],
    trace: list[Any],
    code: str,
    engine: str,
    script_kind: str = "node",
) -> None:
    """Finish explore: either codegen → awaiting_confirm, or agent_once → TestCaseRun."""
    mode = execution_mode_of(plan if isinstance(plan, dict) else session.plan_json)
    session.trace_json = trace
    steps = trace if isinstance(trace, list) else []

    if mode == "agent_once":
        session.playwright_code = ""
        report = _attach_explore_report(
            session, plan=plan, steps=steps, script="", engine=engine
        )
        mid_n = int((report.get("summary") or {}).get("midscene_steps") or 0)
        pw_n = int((report.get("summary") or {}).get("playwright_steps") or 0)
        case = db.get(TestCase, session.testcase_id) if session.testcase_id else None
        judgement = judge_agent_once(trace if isinstance(trace, list) else [], case)
        result, err = judgement.status, judgement.reason
        _emit_commit(
            db,
            session,
            node="explore",
            message=(
                f"Agent 直跑完成：Playwright {pw_n} 步 / Midscene {mid_n} 步；结果 {result}"
                + (f"（{err}）" if err else "")
            ),
            level="error" if result != "passed" else "info",
        )
        get_event_bus().append(session.thread_id, {"type": "explore_report", "report": report})
        run = finalize_agent_once(
            db,
            session,
            status=result,
            error_message=err,
            summary="Agent 直跑通过" if result == "passed" else (err or "Agent 直跑未通过"),
            actual_result=err or ("已按用例完成探索断言" if result == "passed" else ""),
            screenshot_keys=_screenshot_keys_from_events(session),
            judgement=judgement,
        )
        assistant = (
            f"Agent 直跑已结束：{'通过' if result == 'passed' else '失败'}。"
            + (f" {err}" if err else "")
            + (f" 执行记录 #{run.id}。" if run is not None else "")
        )
        _append_message(session, "assistant", assistant)
        session.status = "done" if result == "passed" else ("failed" if result == "failed" else session.status)
        # Normalize: use done for both pass/fail so clients poll until settled
        if session.status not in ("error", "cancelled"):
            session.status = "done"
        if not session.error_message and result != "passed":
            session.error_message = err or "Agent 直跑失败"
        get_event_bus().append(
            session.thread_id,
            {
                "type": "done",
                "status": "done",
                "case_run_id": run.id if run else None,
                "case_run_status": result,
            },
        )
        get_event_bus().append(session.thread_id, {"type": "plan", "plan": session.plan_json})
        db.commit()
        return

    session.playwright_code = code
    review_meta = _maybe_review_case_scripts(db, session, plan=plan)
    # Prefer first ready script after review (may have been fixed).
    case_scripts = plan.get("case_scripts") if isinstance(plan.get("case_scripts"), list) else []
    ready_codes = [
        str(c.get("code") or "")
        for c in case_scripts
        if isinstance(c, dict) and c.get("ready") and str(c.get("code") or "").strip()
    ]
    if ready_codes:
        code = ready_codes[0]
        session.playwright_code = code
        plan["case_scripts"] = case_scripts
        session.plan_json = plan

    report = _attach_explore_report(
        session, plan=plan, steps=steps, script=code, engine=engine
    )
    mid_n = int((report.get("summary") or {}).get("midscene_steps") or 0)
    pw_n = int((report.get("summary") or {}).get("playwright_steps") or 0)
    _emit_commit(
        db,
        session,
        node="explore",
        message=f"探索完成：Playwright {pw_n} 步 / Midscene {mid_n} 步；已生成 explore report 与脚本",
    )
    get_event_bus().append(session.thread_id, {"type": "explore_report", "report": report})
    if not (code or "").strip():
        session.error_message = "探索未产生可执行脚本"
    if script_kind == "python":
        assistant = (
            "已生成探索报告与 Python 脚本（snapshot 探索）。"
            "左侧可查看用例矩阵 / driver 与脚本；确认后才会写入 UI 用例。"
        )
    else:
        assistant = (
            "已生成探索报告与 Node 脚本（稳定步用 Playwright，动态步用 Midscene）。"
            "左侧可查看用例矩阵动作与脚本；确认后才会写入 UI 用例。"
        )
    if review_meta and not review_meta.get("skipped"):
        summary = str(review_meta.get("summary") or "").strip()
        fixed_n = int(review_meta.get("fixed_count") or 0)
        issues = review_meta.get("issues") if isinstance(review_meta.get("issues"), list) else []
        if summary:
            assistant += f"\n脚本自审：{summary}"
        if fixed_n:
            assistant += " 可点「试跑」验证修正后的脚本。"
        elif issues:
            assistant += " 请核对左侧脚本后试跑或继续修改。"
    _append_message(session, "assistant", assistant)
    session.status = "awaiting_confirm"
    get_event_bus().append(
        session.thread_id,
        {"type": "interrupted", "message": "等待确认写入 UI 用例"},
    )
    get_event_bus().append(session.thread_id, {"type": "plan", "plan": session.plan_json})
    db.commit()


def _maybe_review_case_scripts(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    plan: dict[str, Any],
) -> dict[str, Any] | None:
    """Run AI script review when case_scripts exist; mutate plan in place."""
    case_scripts = plan.get("case_scripts") if isinstance(plan.get("case_scripts"), list) else []
    if not any(isinstance(c, dict) and str(c.get("code") or "").strip() for c in case_scripts):
        return None
    checklist = plan.get("checklist") if isinstance(plan.get("checklist"), list) else []
    matrix = plan.get("matrix") if isinstance(plan.get("matrix"), list) else []
    explore_report = (
        plan.get("explore_report") if isinstance(plan.get("explore_report"), dict) else None
    )
    _emit_commit(db, session, node="review", message="正在审核生成的用例脚本…")
    try:
        llm_cfg = get_llm_runtime(db)
        updated, meta = review_and_fix_case_scripts(
            case_scripts,
            goal=str(plan.get("goal") or session.prompt or ""),
            checklist=[str(x) for x in checklist],
            matrix=matrix,
            explore_report=explore_report,
            llm_cfg=llm_cfg,
        )
    except Exception as ex:  # noqa: BLE001
        log.warning("script review hook failed session=%s: %s", session.id, ex)
        _emit_commit(
            db,
            session,
            node="review",
            message=f"脚本自审跳过：{ex}",
            level="warn",
        )
        return {"skipped": True, "summary": str(ex), "issues": [], "fixed_count": 0}

    plan["case_scripts"] = updated
    plan["_script_review"] = {
        "summary": meta.get("summary") or "",
        "issues": meta.get("issues") or [],
        "fixed_count": int(meta.get("fixed_count") or 0),
        "skipped": bool(meta.get("skipped")),
    }
    session.plan_json = plan
    level = "warn" if meta.get("skipped") or meta.get("issues") else "info"
    _emit_commit(
        db,
        session,
        node="review",
        message=str(meta.get("summary") or "脚本自审完成")[:2000],
        level=level,
        data={"script_review": plan["_script_review"]},
    )
    review_ev = {
        "type": "script_review",
        "summary": plan["_script_review"]["summary"],
        "issues": plan["_script_review"]["issues"],
        "fixed_count": plan["_script_review"]["fixed_count"],
    }
    _persist_event(session, review_ev)
    get_event_bus().append(session.thread_id, review_ev)
    return meta


def execute_session_explore(
    db: Session,
    session_id: int,
    *,
    llm_cfg: LlmRuntime | None = None,
    browser_factory: Callable[[], Any] | None = None,
) -> None:
    """Plan + browser explore; ends in awaiting_confirm, done (agent_once), or error."""
    session = db.get(UiScenarioAgentSession, session_id)
    if session is None:
        log.warning("ui scenario session %s not found", session_id)
        return
    # Stop writes status=error (not cancelled); never restart a stopped/settled session.
    if session.status in ("cancelled", "confirmed", "done", "awaiting_confirm", "error"):
        return

    session.status = "running"
    session.error_message = ""
    db.commit()

    get_event_bus().replace(session.thread_id, list(session.events_json or []))
    cfg = llm_cfg or get_llm_runtime(db)

    try:
        env = resolve_env(db, session.project_id, session.env_id)
        base_url = (env.base_url or "").strip()
        if not base_url:
            raise AppError("bad_request", UI_ENV_BASE_URL_REQUIRED, status_code=400)

        prior_plan = session.plan_json if isinstance(session.plan_json, dict) else {}
        creds = merge_credentials(
            session.credentials_json if isinstance(session.credentials_json, dict) else None
        )
        ctx = _build_ctx(env, None)
        if not ctx.get("username") and ctx.get("user"):
            ctx["username"] = str(ctx["user"])
        ctx.update(credentials_to_ctx(creds))
        browser_auth = resolve_plan_browser_auth(env, prior_plan)
        if (
            plan_auth_mode(prior_plan) != "none"
            and env_has_inject_config(env)
            and not (browser_auth.extra_http_headers or browser_auth.local_storage)
        ):
            raise AppError("bad_request", UI_ENV_TOKEN_REQUIRED, status_code=400)

        _emit_commit(db, session, node="plan", message="正在生成探索计划…")

        extra_prompt = (session.prompt or "").strip()
        manual_steps: list[dict[str, str]] = []
        title = extra_prompt or "UI 探索"
        if session.testcase_id:
            tc = (
                db.query(TestCase)
                .options(joinedload(TestCase.steps))
                .filter(TestCase.id == session.testcase_id, TestCase.project_id == session.project_id)
                .first()
            )
            if tc is None:
                raise AppError("not_found", TEST_CASE_NOT_FOUND, status_code=404)
            title = tc.title
            td_text = case_test_data_text(variables=tc.variables_json, test_data=tc.test_data)
            # Structured variables win over prose credential scrape
            case_vars = as_str_dict(tc.variables_json)
            if case_vars:
                ctx.update(case_vars)
            manual_steps = [{"action": s.action, "expected": s.expected} for s in (tc.steps or [])]
            # Do not regex-scrape username/password from case prose into ctx — Agent reads
            # credentials from plan/test_data/steps in decide. Keep variables_json + explicit creds only.
            plan = build_plan_from_testcase(
                title=tc.title,
                steps=manual_steps,
                base_url=base_url,
                llm_cfg=cfg,
                test_data=td_text or None,
                overall_expected=tc.overall_expected,
                precondition=tc.precondition,
                extra_prompt=extra_prompt,
                skip_login=browser_auth.skip_login,
            )
            plan["case_name"] = tc.title
        else:
            plan = build_plan_from_prompt(prompt=session.prompt, base_url=base_url, llm_cfg=cfg)

        max_steps = DEFAULT_MAX_STEPS
        headed = False
        engine = "midscene"
        if isinstance(prior_plan.get("_max_steps"), int):
            max_steps = max(1, min(50, int(prior_plan["_max_steps"])))
        headed = bool(prior_plan.get("_headed"))
        raw_engine = str(prior_plan.get("engine") or "midscene")
        if raw_engine in ("midscene", "playwright"):
            engine = raw_engine
        plan["_max_steps"] = max_steps
        plan["_headed"] = headed
        plan["engine"] = engine
        # Preserve session auth (none = login / skip inject); default follow-env.
        preserved_auth = plan_auth_mode(prior_plan)
        plan["_auth_mode"] = preserved_auth
        plan["_account_name"] = (
            str(prior_plan.get("_account_name") or "").strip() or None
            if preserved_auth == "account"
            else None
        )
        plan["_execution_mode"] = execution_mode_of(prior_plan)
        if prior_plan.get("_case_run_id") is not None:
            plan["_case_run_id"] = prior_plan.get("_case_run_id")
        plan["user_prompt"] = extra_prompt or title
        for key, value in prior_plan.items():
            if str(key).startswith("_repair_"):
                plan[key] = value
        if prior_plan.get("_single_case"):
            plan["_single_case"] = True
        # Keep explicit empty matrix for explore-first; only normalize when rows exist
        if isinstance(plan.get("matrix"), list) and plan["matrix"]:
            plan = ensure_plan_matrix(plan)
        else:
            plan = dict(plan)
            plan.setdefault("matrix", [])
            plan.setdefault("case_scripts", [])
        session.plan_json = plan
        _emit_commit(db, session, node="plan", message=f"目标：{plan.get('goal') or title}")
        exec_mode = execution_mode_of(plan)
        if exec_mode == "agent_once":
            checks = plan.get("checklist") if isinstance(plan.get("checklist"), list) else []
            hint = f"；检查点 {len(checks)} 项" if checks else ""
            _emit_commit(
                db,
                session,
                node="plan",
                message=f"Agent 直跑：按功能用例执行{hint}，不生成脚本",
            )
        elif plan.get("_single_case"):
            checks = plan.get("checklist") if isinstance(plan.get("checklist"), list) else []
            hint = f"；检查点 {len(checks)} 项" if checks else ""
            _emit_commit(
                db,
                session,
                node="plan",
                message=f"按当前功能用例探索{hint}，完成后生成一条 UI 脚本",
            )
        elif isinstance(plan.get("matrix"), list) and plan["matrix"]:
            _emit_matrix_summary(db, session, plan)
        else:
            checks = plan.get("checklist") if isinstance(plan.get("checklist"), list) else []
            hint = f"；检查点 {len(checks)} 项" if checks else ""
            _emit_commit(
                db,
                session,
                node="plan",
                message=f"先探索界面{hint}，完成后生成多条用例脚本",
            )

        timeout_ms = int(getattr(env, "default_timeout_ms", None) or 15000)
        browser_name = session.browser or env.browser or "chromium"
        mode = "有头" if headed else "无头"
        engine_label = {
            "playwright": "playwright",
            "midscene": "playwright+midscene",
        }.get(engine, engine)
        _emit_commit(
            db,
            session,
            node="explore",
            message=f"开始浏览器探索（{engine_label} · {browser_name} · {mode}）…",
        )
        # Default path: playwright-cli. browser_factory keeps in-process Playwright for tests.
        if engine in ("midscene", "playwright") and browser_factory is None:
            _explore_with_engine(
                db,
                session,
                engine=engine,
                env=env,
                plan=plan,
                ctx=ctx,
                llm_cfg=cfg,
                base_url=base_url,
                browser_name=browser_name,
                headed=headed,
                timeout_ms=timeout_ms,
                max_steps=max_steps,
                title=title,
                browser_factory=browser_factory,
                browser_auth=browser_auth,
            )
            return

        def _cancelled() -> bool:
            db.refresh(session)
            return session.status not in ("running", "awaiting_human")

        def _on_step(item: dict[str, Any]) -> None:
            act = item.get("action")
            st = item.get("status")
            sel = item.get("selector") or ""
            val = item.get("value")
            err = item.get("error")
            extra = ""
            if sel:
                extra += f" sel={sel}"
            if val is not None and act in ("goto", "fill", "press", "select_option"):
                extra += f" value={val}"
            if err:
                extra += f" err={err}"
            _emit_commit(
                db,
                session,
                node="explore",
                message=f"{act} → {st}{extra}",
                level="error" if st == "error" else "info",
            )

        cdp_endpoint: str | None = None
        mid_cfg: dict[str, str] | None = None
        try:
            mid_cfg = get_midscene_runtime(db).as_model_config()
        except Exception:  # noqa: BLE001
            mid_cfg = None

        if browser_factory is not None:
            page = browser_factory()
            trace = run_explore_loop(
                page=page,
                base_url=base_url,
                plan=plan,
                ctx=ctx,
                llm_cfg=cfg,
                max_steps=max_steps,
                timeout_ms=timeout_ms,
                should_cancel=_cancelled,
                on_step=_on_step,
                cdp_endpoint=None,
                model_config=mid_cfg,
            )
        else:
            from playwright.sync_api import sync_playwright

            from app.services.ui_automation.node_ui_runner import (
                cdp_endpoint_for_port,
                chromium_launch_args,
                free_cdp_port,
            )

            with sync_playwright() as p:
                use_name = browser_name if browser_name in ("chromium", "firefox", "webkit") else "chromium"
                launcher = getattr(p, use_name)
                launch_kwargs: dict[str, Any] = {"headless": not headed}
                if use_name == "chromium":
                    cdp_port = free_cdp_port()
                    launch_kwargs["args"] = chromium_launch_args(cdp_port=cdp_port)
                    cdp_endpoint = cdp_endpoint_for_port(cdp_port)
                browser = launcher.launch(**launch_kwargs)
                try:
                    vp = env.viewport_json if isinstance(env.viewport_json, dict) else {"width": 1280, "height": 720}
                    page = browser.new_page(
                        viewport={
                            "width": int(vp.get("width") or 1280),
                            "height": int(vp.get("height") or 720),
                        }
                    )
                    page.set_default_timeout(timeout_ms)
                    trace = run_explore_loop(
                        page=page,
                        base_url=base_url,
                        plan=plan,
                        ctx=ctx,
                        llm_cfg=cfg,
                        max_steps=max_steps,
                        timeout_ms=timeout_ms,
                        should_cancel=_cancelled,
                        on_step=_on_step,
                        cdp_endpoint=cdp_endpoint,
                        model_config=mid_cfg,
                    )
                finally:
                    browser.close()

        if _cancelled():
            if session.status != "error":
                session.status = "error"
                session.error_message = MANUAL_STOP_MESSAGE
            session.trace_json = trace
            _emit_commit(
                db, session, node="explore", message=MANUAL_STOP_MESSAGE, level="warn"
            )
            _finalize_agent_once_on_failure(
                db, session, status="cancelled", error_message=MANUAL_STOP_MESSAGE
            )
            get_event_bus().append(
                session.thread_id, {"type": "error", "message": MANUAL_STOP_MESSAGE}
            )
            get_event_bus().append(session.thread_id, {"type": "done", "status": "error"})
            db.commit()
            return

        steps = trace_to_ui_steps(trace)
        # Preserve locator hints from explore loop for driver inference
        for i, step in enumerate(steps):
            if i < len(trace) and isinstance(trace[i], dict):
                for key in ("locate", "placeholder", "role", "name", "reason", "driver", "prompt", "kind"):
                    if trace[i].get(key) and key not in step:
                        step[key] = trace[i].get(key)
        code = ""
        if execution_mode_of(plan) != "agent_once":
            code = trace_to_explore_script(
                title=title,
                base_url=base_url,
                trace=trace,
                start_path=str(plan.get("start_path") or "/"),
                username=str(ctx.get("username") or ""),
                password=str(ctx.get("password") or ""),
            )
        _complete_explore(
            db,
            session,
            plan=plan,
            trace=steps,
            code=code,
            engine="playwright",
            script_kind="python",
        )
    except AppError as ex:
        session.status = "error"
        session.error_message = ex.message
        _emit_commit(db, session, node="error", message=ex.message, level="error")
        get_event_bus().append(session.thread_id, {"type": "error", "message": ex.message})
        _finalize_agent_once_on_failure(db, session, status="error", error_message=ex.message)
        db.commit()
    except Exception as ex:  # noqa: BLE001
        log.exception("ui scenario explore failed id=%s", session_id)
        session.status = "error"
        session.error_message = str(ex)
        _emit_commit(db, session, node="error", message=str(ex), level="error")
        get_event_bus().append(session.thread_id, {"type": "error", "message": str(ex)})
        _finalize_agent_once_on_failure(db, session, status="error", error_message=str(ex))
        db.commit()
    finally:
        try:
            db.refresh(session)
            _maybe_complete_repair_explore(db, session)
        except Exception:  # noqa: BLE001
            log.exception("ui repair explore complete failed id=%s", session_id)


def _maybe_complete_repair_explore(db: Session, session: UiScenarioAgentSession) -> None:
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    if not plan.get("_repair_fix_id"):
        return
    from app.services.ui_automation.ui_run_fix import complete_repair_from_explore

    complete_repair_from_explore(db, session)


def apply_user_message(
    db: Session,
    session: UiScenarioAgentSession,
    content: str,
    *,
    llm_cfg: LlmRuntime | None = None,
    browser_factory: Callable[[], Any] | None = None,
    enqueue_explore: Callable[[int, str], str | None] | None = None,
) -> UiScenarioAgentSession:
    if session.status not in ("awaiting_confirm", "error", "awaiting_human"):
        raise AppError("bad_request", f"当前状态不可继续对话: {session.status}", status_code=400)

    text = content.strip()
    _append_message(session, "user", text)

    new_creds = extract_credentials_from_text(text)
    if new_creds:
        session.credentials_json = merge_credentials(
            session.credentials_json if isinstance(session.credentials_json, dict) else None,
            new_creds,
        )
        _emit(session, node="credentials", message="已从对话更新账号信息（密码不回显）")

    if session.status == "awaiting_human":
        plan = dict(session.plan_json) if isinstance(session.plan_json, dict) else {}
        plan["_human_reply"] = text
        session.plan_json = plan
        _append_message(session, "assistant", "已收到，浏览器中继续探索…")
        _emit(session, node="explore", message="已收到人工协助回复")
        db.commit()
        db.refresh(session)
        return session

    cfg = llm_cfg or get_llm_runtime(db)
    from_error = session.status == "error"
    want_reexplore = from_error or bool(_REEXPLORE_HINT.search(text))

    if want_reexplore:
        if from_error and text:
            prev = (session.prompt or "").strip()
            session.prompt = f"{prev}\n{text}".strip() if prev else text
        session.status = "running"
        session.dry_run_status = None
        session.dry_run_error = ""
        session.error_message = ""
        _append_message(session, "assistant", "好的，将按最新对话重新探索页面…")
        db.commit()
        if enqueue_explore is not None:
            env = resolve_env(db, session.project_id, session.env_id)
            task_id = enqueue_explore(session.id, env.queue_name or "ui")
            if task_id:
                session.celery_task_id = task_id
                db.commit()
        else:
            execute_session_explore(db, session.id, llm_cfg=cfg, browser_factory=browser_factory)
        db.refresh(session)
        return session

    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    _emit(session, node="revise", message="正在根据对话修订脚本…")
    db.commit()
    try:
        revised = revise_script_with_llm(
            script=session.playwright_code or "",
            instruction=text,
            plan=plan,
            llm_cfg=cfg,
        )
        if not is_runnable_ui_script(revised):
            raise ValueError("修订后脚本无效")
        session.playwright_code = revised
        _append_message(session, "assistant", "已按你的要求改好脚本。可继续修改或确认写入。")
        session.status = "awaiting_confirm"
        db.commit()
    except Exception as ex:  # noqa: BLE001
        _append_message(session, "assistant", f"修订失败：{ex}。可换一种说法，或说「重新探索」。")
        session.status = "awaiting_confirm"
        _emit(session, node="revise", message=str(ex), level="error")
        db.commit()
    db.refresh(session)
    return session


def stop_session(db: Session, session: UiScenarioAgentSession) -> UiScenarioAgentSession:
    """Abort a running explore; leave session in error so the user can continue chatting."""
    if session.status not in ("running", "awaiting_human"):
        if session.status == "error" and (session.error_message or "").startswith(MANUAL_STOP_MESSAGE):
            db.refresh(session)
            return session
        raise AppError("bad_request", f"当前状态不可停止: {session.status}", status_code=400)

    plan = dict(session.plan_json) if isinstance(session.plan_json, dict) else {}
    plan.pop("_human_reply", None)
    session.plan_json = plan
    session.status = "error"
    session.error_message = MANUAL_STOP_MESSAGE
    _append_message(session, "assistant", f"{MANUAL_STOP_MESSAGE}，可继续描述后重试。")
    _emit(session, node="explore", message=MANUAL_STOP_MESSAGE, level="warn")
    get_event_bus().append(session.thread_id, {"type": "error", "message": MANUAL_STOP_MESSAGE})
    get_event_bus().append(session.thread_id, {"type": "done", "status": "error"})
    _finalize_agent_once_on_failure(
        db, session, status="cancelled", error_message=MANUAL_STOP_MESSAGE
    )
    db.commit()

    task_id = (session.celery_task_id or "").strip()
    if task_id:
        try:
            from app.tasks.celery_app import celery_app

            # Soft revoke only: terminate=True with --pool=solo can kill the worker process.
            # Cooperative cancel polls DB status via should_cancel / _should_cancel.
            celery_app.control.revoke(task_id, terminate=False)
        except Exception:  # noqa: BLE001
            log.warning("revoke ui scenario task failed task_id=%s", task_id, exc_info=True)

    db.refresh(session)
    return session


def confirm_session(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    action: str,
    name: str | None = None,
    script: str | None = None,
    case_status: str = "ready",
    user_id: int | None = None,
) -> UiScenarioAgentSession:
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    if execution_mode_of(plan) == "agent_once":
        raise AppError(
            "bad_request",
            "Agent 直跑模式不会写入 UI 用例，请查看功能用例执行记录",
            status_code=400,
        )
    if plan.get("_repair_fix_id"):
        raise AppError(
            "bad_request",
            "修复探索请在失败记录中确认建议，不会单独写入新用例",
            status_code=400,
        )
    if session.status != "awaiting_confirm":
        raise AppError("bad_request", f"会话状态不可确认: {session.status}", status_code=400)

    if action == "cancel":
        session.status = "cancelled"
        _append_message(session, "assistant", "已取消本次会话，未写入 UI 用例。可点「新会话」重新开始。")
        get_event_bus().append(session.thread_id, {"type": "done", "status": "cancelled"})
        db.commit()
        db.refresh(session)
        return session

    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    case_scripts = plan.get("case_scripts") if isinstance(plan.get("case_scripts"), list) else []
    ready_cases = [
        c
        for c in case_scripts
        if isinstance(c, dict) and c.get("ready") and is_runnable_ui_script(str(c.get("code") or ""))
    ]

    chosen = (script or "").strip() or (session.playwright_code or "").strip()
    if not ready_cases:
        if not chosen:
            legacy = list(session.result_steps_json) if isinstance(session.result_steps_json, list) else []
            chosen = steps_to_script(legacy).strip()
        if not is_runnable_ui_script(chosen):
            raise AppError("bad_request", "没有可写入的脚本", status_code=400)
        ready_cases = [
            {
                "id": "UC-001",
                "name": (
                    (name or "").strip()
                    or (str(plan.get("case_name") or "").strip() if plan else "")
                    or (str(plan.get("goal") or "").strip() if plan else "")
                    or f"UI Agent #{session.id}"
                ),
                "code": chosen,
                "ready": True,
            }
        ]
    session.playwright_code = str(ready_cases[0].get("code") or chosen)

    def _normalize_case_steps(raw: list[Any] | None) -> list[dict[str, Any]] | None:
        out: list[dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip()
            if not action or action == "done":
                continue
            step = dict(item)
            step.pop("screenshot_key", None)
            step.pop("screenshot", None)
            out.append(step)
        return out or None

    def _steps_for_case(case_meta: dict[str, Any]) -> list[dict[str, Any]] | None:
        raw = case_meta.get("steps") if isinstance(case_meta.get("steps"), list) else None
        if not raw:
            matrix = plan.get("matrix") if isinstance(plan.get("matrix"), list) else []
            cid = str(case_meta.get("id") or "")
            for row in matrix:
                if isinstance(row, dict) and str(row.get("id") or "") == cid:
                    if isinstance(row.get("steps"), list):
                        raw = row["steps"]
                    break
        if not raw:
            raw = session.result_steps_json if isinstance(session.result_steps_json, list) else None
        return _normalize_case_steps(raw if isinstance(raw, list) else None)

    saved_ids: list[int] = []
    for c in ready_cases:
        code = str(c.get("code") or "").strip()
        if not is_runnable_ui_script(code):
            continue
        title = f"{c.get('id') or 'UC'}: {c.get('name') or 'UI 用例'}".strip(": ")
        if name and len(ready_cases) == 1:
            title = (name or "").strip() or title
        case = UiCase(
            project_id=session.project_id,
            name=title[:512],
            description=f"由 UI 自动化 Agent 会话 #{session.id} 生成",
            script=code,
            steps_json=_steps_for_case(c),
            # Do not invent case variables; user may set them later on the case form.
            variables_json=None,
            auth_mode=plan_auth_mode(plan if isinstance(plan, dict) else None),
            manual_case_id=session.testcase_id,
            status=case_status if case_status in ("draft", "ready") else "ready",
            creator_id=user_id or session.user_id,
        )
        db.add(case)
        db.flush()
        saved_ids.append(int(case.id))
    if not saved_ids:
        raise AppError("bad_request", "没有可写入的脚本", status_code=400)
    db.commit()

    session.saved_ui_case_id = saved_ids[0]
    if isinstance(session.plan_json, dict):
        session.plan_json = {**session.plan_json, "saved_ui_case_ids": saved_ids}
    session.status = "confirmed"
    ids_text = "、".join(f"#{i}" for i in saved_ids)
    _append_message(session, "assistant", f"已确认并写入 {len(saved_ids)} 条 UI 用例：{ids_text}。")
    get_event_bus().append(
        session.thread_id,
        {
            "type": "done",
            "status": "confirmed",
            "saved_ui_case_id": saved_ids[0],
            "saved_ui_case_ids": saved_ids,
        },
    )
    db.commit()

    db.refresh(session)
    return session


def _resolve_trial_script(
    session: UiScenarioAgentSession,
    *,
    case_id: str,
    script: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (case_id, code, case_meta) for trial run."""
    override = (script or "").strip()
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    case_scripts = plan.get("case_scripts") if isinstance(plan.get("case_scripts"), list) else []
    wanted = (case_id or "").strip()
    chosen: dict[str, Any] | None = None
    if wanted:
        for row in case_scripts:
            if isinstance(row, dict) and str(row.get("id") or "").strip() == wanted:
                chosen = row
                break
    if chosen is None and case_scripts:
        for row in case_scripts:
            if isinstance(row, dict) and row.get("ready") and str(row.get("code") or "").strip():
                chosen = row
                break
    if chosen is None:
        code = override or (session.playwright_code or "").strip()
        if not is_runnable_ui_script(code):
            raise AppError("bad_request", "没有可试跑的脚本", status_code=400)
        return wanted or "script", code, {"id": wanted or "script", "name": "当前脚本", "code": code}

    cid = str(chosen.get("id") or "").strip() or wanted or "TC"
    code = override or str(chosen.get("code") or "").strip()
    if not is_runnable_ui_script(code):
        raise AppError("bad_request", f"用例 {cid} 没有可执行脚本", status_code=400)
    return cid, code, chosen


def enqueue_session_trial_run(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    case_id: str = "",
    headed: bool = True,
    script: str | None = None,
    enqueue: Callable[[int, str], str | None] | None = None,
) -> UiScenarioAgentSession:
    """Mark session dry_run running and enqueue Celery trial task."""
    if session.status not in ("awaiting_confirm", "confirmed", "done"):
        raise AppError(
            "bad_request",
            f"当前状态不可试跑: {session.status}",
            status_code=400,
        )
    if session.dry_run_status == "running":
        raise AppError("bad_request", "试跑进行中，请稍候", status_code=400)

    cid, code, meta = _resolve_trial_script(session, case_id=case_id, script=script)
    plan = dict(session.plan_json) if isinstance(session.plan_json, dict) else {}
    plan["_trial"] = {
        "case_id": cid,
        "case_name": str(meta.get("name") or cid),
        "headed": bool(headed),
        "script": code,
    }
    session.plan_json = plan
    session.dry_run_status = "running"
    session.dry_run_error = ""
    mode = "有头" if headed else "无头"
    _emit(
        session,
        node="trial",
        message=f"开始试跑 {cid}（{mode}）…",
    )
    trial_start = {
        "type": "trial_start",
        "case_id": cid,
        "headed": bool(headed),
    }
    _persist_event(session, trial_start)
    get_event_bus().append(session.thread_id, trial_start)
    db.commit()

    env = resolve_env(db, session.project_id, session.env_id)
    queue = env.queue_name or "ui"
    if enqueue is not None:
        task_id = enqueue(session.id, queue)
    else:
        from app.tasks.ui_automation.run_ui_scenario_agent import enqueue_ui_scenario_agent_trial

        task_id = enqueue_ui_scenario_agent_trial(session.id, queue=queue)
    if task_id:
        session.celery_task_id = task_id
        db.commit()
    db.refresh(session)
    return session


def execute_session_trial_run(db: Session, session_id: int) -> None:
    """Run one case script from plan._trial via execute_ui_script; update dry_run_*."""
    from app.services.ui_automation.ui_runner import execute_ui_script

    session = db.get(UiScenarioAgentSession, session_id)
    if session is None:
        log.warning("ui scenario trial session %s not found", session_id)
        return
    plan = dict(session.plan_json) if isinstance(session.plan_json, dict) else {}
    trial = plan.get("_trial") if isinstance(plan.get("_trial"), dict) else {}
    cid = str(trial.get("case_id") or "").strip() or "script"
    headed = bool(trial.get("headed"))
    code = str(trial.get("script") or "").strip()
    if not code:
        try:
            cid, code, _ = _resolve_trial_script(session, case_id=cid)
        except AppError as ex:
            session.dry_run_status = "error"
            session.dry_run_error = ex.message
            _emit(session, node="trial", message=ex.message, level="error")
            get_event_bus().append(
                session.thread_id,
                {"type": "trial_done", "status": "error", "case_id": cid, "error": ex.message},
            )
            db.commit()
            return

    get_event_bus().replace(session.thread_id, list(session.events_json or []))

    def _finish_trial(
        *,
        status: str,
        error: str = "",
        steps: int = 0,
        level: str = "info",
        assistant: str = "",
    ) -> None:
        session.dry_run_status = status
        session.dry_run_error = error
        plan["_trial"] = {**trial, "status": status, "error": error, "steps": steps}
        session.plan_json = plan
        msg = assistant or (
            f"试跑 {cid} {status}" + (f"：{error}" if error else "")
        )
        _emit(session, node="trial", message=msg[:2000], level=level)
        done_ev = {
            "type": "trial_done",
            "status": status,
            "case_id": cid,
            **({"error": error} if error else {}),
        }
        _persist_event(session, done_ev)
        get_event_bus().append(session.thread_id, done_ev)
        if assistant:
            _append_message(session, "assistant", assistant)
        db.commit()

    try:
        env = resolve_env(db, session.project_id, session.env_id)
        base_url = (env.base_url or "").strip()
        if not base_url:
            raise AppError("bad_request", UI_ENV_BASE_URL_REQUIRED, status_code=400)

        creds = merge_credentials(
            session.credentials_json if isinstance(session.credentials_json, dict) else None
        )
        ctx = _build_ctx(env, None)
        if not ctx.get("username") and ctx.get("user"):
            ctx["username"] = str(ctx["user"])
        ctx.update(credentials_to_ctx(creds))
        ctx = script_env_from_ctx(ctx)

        browser_auth = resolve_plan_browser_auth(env, plan)

        timeout_ms = int(getattr(env, "default_timeout_ms", None) or 15000)
        browser_name = session.browser or env.browser or "chromium"
        vp = env.viewport_json if isinstance(env.viewport_json, dict) else {"width": 1280, "height": 720}
        viewport = {"width": int(vp.get("width") or 1280), "height": int(vp.get("height") or 720)}

        def _on_log(message: str) -> None:
            text = str(message or "").strip()
            if not text:
                return
            _emit_commit(db, session, node="trial", message=text[:2000])

        step_rows = execute_ui_script(
            base_url=base_url,
            script=code,
            ctx=ctx,
            viewport=viewport,
            headless=not headed,
            browser_name=browser_name,
            default_timeout_ms=timeout_ms,
            storage_state_path=None,
            extra_http_headers=browser_auth.extra_http_headers,
            local_storage=browser_auth.local_storage,
            on_log=_on_log,
        )
        bad = [
            r
            for r in step_rows
            if isinstance(r, dict) and str(r.get("status") or "") in ("failed", "error")
        ]
        if bad:
            last = bad[-1]
            err = str(last.get("error_message") or last.get("status") or "试跑失败")
            status = "failed" if str(last.get("status")) == "failed" else "error"
            _finish_trial(
                status=status,
                error=err,
                steps=len(step_rows),
                level="error",
                assistant=f"试跑 {cid} 未通过：{err}",
            )
        else:
            _finish_trial(
                status="passed",
                steps=len(step_rows),
                assistant=f"试跑 {cid} 通过。",
            )
    except AppError as ex:
        _finish_trial(
            status="error",
            error=ex.message,
            level="error",
            assistant=f"试跑失败：{ex.message}",
        )
    except Exception as ex:  # noqa: BLE001
        log.exception("ui scenario trial failed id=%s", session_id)
        _finish_trial(
            status="error",
            error=str(ex),
            level="error",
            assistant=f"试跑出错：{ex}",
        )
