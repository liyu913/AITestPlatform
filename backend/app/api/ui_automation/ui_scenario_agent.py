"""API routes for conversational UI scenario agent."""

from __future__ import annotations

import json
from collections.abc import Iterator

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.deps import SessionLocal, get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.ui_automation.ui_automation import UiScenarioAgentSession
from app.models.identity.user import User
from app.schemas.ui_automation.ui_scenario_agent import (
    UiScenarioAgentConfirm,
    UiScenarioAgentMessage,
    UiScenarioAgentSessionCreate,
    UiScenarioAgentSessionOut,
    UiScenarioAgentSessionSummary,
    UiScenarioAgentTrialRun,
)
from app.services.api_automation.api_scenario_events import get_event_bus
from app.services.ai.effective_ai_settings import get_llm_runtime, get_midscene_runtime
from app.services.functional.cycle_attempt_sync import validate_cycle_item
from app.services.functional.testcase_run_finalize import (
    case_run_id_of,
    create_agent_once_run,
    execution_mode_of,
)
from app.services.ui_automation.ui_scenario_agent import (
    apply_user_message,
    confirm_session,
    enqueue_session_trial_run,
    extract_credentials_from_text,
    merge_credentials,
    new_thread_id,
    plan_auth_mode,
    resolve_env,
    resolve_plan_browser_auth,
    stop_session,
    _emit_commit,
)
from app.services.file_storage import absolute_path_for_storage_key
from app.services.ui_automation.ui_env_auth import (
    env_has_inject_config,
    normalize_auth_mode,
)
from app.tasks.ui_automation.run_ui_scenario_agent import (
    enqueue_ui_scenario_agent,
    enqueue_ui_scenario_agent_trial,
)
from app.user_messages import (
    TEST_CASE_NOT_FOUND,
    UI_ENV_TOKEN_REQUIRED,
    UI_SCREENSHOT_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/ui-scenario-agent", tags=["ui-scenario-agent"])

_db_factory_override = None
_explore_runner_override = None  # Callable[[Session, int], None] for tests
_trial_runner_override = None  # Callable[[Session, int], None] for tests


def set_db_factory(factory) -> None:
    global _db_factory_override
    _db_factory_override = factory


def set_explore_runner(runner) -> None:
    """Test hook: run explore inline instead of Celery."""
    global _explore_runner_override
    _explore_runner_override = runner


def set_trial_runner(runner) -> None:
    """Test hook: run trial inline instead of Celery."""
    global _trial_runner_override
    _trial_runner_override = runner


def _resolve_db() -> Session:
    if _db_factory_override is not None:
        return _db_factory_override()
    return SessionLocal()


def _session_out(row: UiScenarioAgentSession) -> UiScenarioAgentSessionOut:
    messages = row.messages_json if isinstance(row.messages_json, list) else []
    db_events = row.events_json if isinstance(row.events_json, list) else []
    live = get_event_bus().snapshot(row.thread_id)
    # Celery worker writes DB; API process bus may be empty — prefer longer list
    events = live if len(live) >= len(db_events) else db_events
    creds = row.credentials_json if isinstance(row.credentials_json, dict) else {}
    has_creds = bool(creds.get("username") or creds.get("password") or creds.get("user"))
    plan = row.plan_json if isinstance(row.plan_json, dict) else {}
    return UiScenarioAgentSessionOut(
        id=row.id,
        project_id=row.project_id,
        user_id=row.user_id,
        thread_id=row.thread_id,
        status=row.status,
        prompt=row.prompt or "",
        testcase_id=row.testcase_id,
        env_id=row.env_id,
        browser=row.browser or "chromium",
        headed=bool(plan.get("_headed")),
        messages=[
            {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
            for m in messages
            if isinstance(m, dict)
        ],
        plan=plan or None,
        matrix=plan.get("matrix") if isinstance(plan.get("matrix"), list) else [],
        explore_report=plan.get("explore_report") if isinstance(plan.get("explore_report"), dict) else None,
        trace=row.trace_json if isinstance(row.trace_json, list) else [],
        result_steps=row.result_steps_json if isinstance(row.result_steps_json, list) else [],
        playwright_code=row.playwright_code or "",
        has_credentials=has_creds,
        events=events,
        dry_run_status=row.dry_run_status,
        dry_run_error=row.dry_run_error or "",
        error_message=row.error_message or "",
        saved_ui_case_id=row.saved_ui_case_id,
        run_id=row.run_id,
        case_run_id=case_run_id_of(plan),
        execution_mode=execution_mode_of(plan),
        auth_mode=plan_auth_mode(plan),
        account_name=(
            str(plan.get("_account_name") or "").strip() or None
            if plan_auth_mode(plan) == "account"
            else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _session_title(prompt: str) -> str:
    text = (prompt or "").strip()
    if not text:
        return "新会话"
    return text[:40] + ("…" if len(text) > 40 else "")


def _require_session(
    db: Session,
    project_id: int,
    session_id: int,
    user_id: int | None = None,
) -> UiScenarioAgentSession:
    q = db.query(UiScenarioAgentSession).filter(
        UiScenarioAgentSession.id == session_id,
        UiScenarioAgentSession.project_id == project_id,
    )
    if user_id is not None:
        q = q.filter(UiScenarioAgentSession.user_id == user_id)
    row = q.first()
    if row is None:
        raise AppError("not_found", "UI Agent 会话不存在", status_code=404)
    return row


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _start_explore(db: Session, row: UiScenarioAgentSession, queue: str) -> None:
    if _explore_runner_override is not None:
        _emit_commit(db, row, node="queue", message="开始探索（测试内联）…")
        _explore_runner_override(db, row.id)
        db.refresh(row)
        return
    try:
        _emit_commit(
            db,
            row,
            node="queue",
            message=f"任务已入队（queue={queue}），等待 Celery Worker 执行…",
        )
        task_id = enqueue_ui_scenario_agent(row.id, queue=queue)
        if task_id:
            row.celery_task_id = task_id
            _emit_commit(db, row, node="queue", message=f"Celery task_id={task_id}")
            db.refresh(row)
        else:
            _emit_commit(
                db,
                row,
                node="queue",
                message="入队未返回 task_id，请确认 Worker 已启动",
                level="warn",
            )
    except Exception as ex:  # noqa: BLE001
        _emit_commit(
            db,
            row,
            node="queue",
            message=f"入队失败: {ex}。请启动: uv run celery -A app.tasks.celery_app worker -l info -Q ui,celery",
            level="error",
        )
        row.status = "error"
        row.error_message = f"Celery 入队失败: {ex}"
        db.commit()
        db.refresh(row)


@router.get("/sessions", response_model=list[UiScenarioAgentSessionSummary])
def list_sessions(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[UiScenarioAgentSessionSummary]:
    rows = (
        db.query(UiScenarioAgentSession)
        .filter(
            UiScenarioAgentSession.project_id == project_id,
            UiScenarioAgentSession.user_id == user.id,
        )
        .order_by(UiScenarioAgentSession.updated_at.desc())
        .all()
    )
    return [
        UiScenarioAgentSessionSummary(
            id=r.id,
            title=_session_title(r.prompt or ""),
            status=r.status or "running",
            updated_at=r.updated_at,
            message_count=len(r.messages_json) if isinstance(r.messages_json, list) else 0,
        )
        for r in rows
    ]


@router.post("/sessions", response_model=UiScenarioAgentSessionOut, status_code=201)
def create_session(
    project_id: int,
    body: UiScenarioAgentSessionCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiScenarioAgentSessionOut:
    llm_cfg = get_llm_runtime(db)
    engine = "midscene"
    if not llm_cfg.api_key.strip():
        raise AppError("bad_request", "未配置 LLM API Key", status_code=400)
    midscene = get_midscene_runtime(db)
    if not midscene.api_key.strip() and not llm_cfg.api_key.strip():
        raise AppError(
            "bad_request",
            "未配置 Midscene 模型 API Key，请在系统 AI 配置中填写",
            status_code=400,
        )

    execution_mode = (
        body.execution_mode
        if body.execution_mode in ("explore_codegen", "agent_once")
        else "explore_codegen"
    )
    if body.testcase_id is not None:
        tc = (
            db.query(TestCase)
            .filter(TestCase.id == body.testcase_id, TestCase.project_id == project_id)
            .first()
        )
        if tc is None:
            raise AppError("not_found", TEST_CASE_NOT_FOUND, status_code=404)
    elif execution_mode == "agent_once":
        raise AppError("bad_request", "Agent 直跑需要选择功能用例", status_code=400)

    env = resolve_env(db, project_id, body.env_id)
    cycle_item = validate_cycle_item(db, project_id, body.cycle_item_id)
    prompt = (body.prompt or "").strip()
    if not prompt and body.testcase_id is None:
        raise AppError("bad_request", "请描述要自动化的流程，或选择一个功能用例", status_code=400)

    # Follow env inject unless auth_mode=none (login / skip inject).
    session_auth = normalize_auth_mode(body.auth_mode)
    if session_auth != "none" and env_has_inject_config(env):
        browser_auth = resolve_plan_browser_auth(
            env, {"_auth_mode": session_auth, "_account_name": body.account_name}
        )
        if not (browser_auth.extra_http_headers or browser_auth.local_storage):
            raise AppError("bad_request", UI_ENV_TOKEN_REQUIRED, status_code=400)

    testdata_creds = (
        extract_credentials_from_text(tc.test_data or "")
        if body.testcase_id is not None
        else {}
    )
    creds = merge_credentials(testdata_creds, extract_credentials_from_text(prompt))
    thread_id = new_thread_id()
    get_event_bus().replace(thread_id, [])

    browser = (body.browser or env.browser or "chromium").strip() or "chromium"
    user_msg = (
        prompt
        or ("请按功能用例直接执行并给出通过/失败" if execution_mode == "agent_once" else "请根据所选功能用例生成 UI 步骤")
    )
    account_name = (
        (body.account_name or "").strip() or None if session_auth == "account" else None
    )
    row = UiScenarioAgentSession(
        project_id=project_id,
        user_id=user.id,
        thread_id=thread_id,
        status="running",
        prompt=prompt,
        testcase_id=body.testcase_id,
        env_id=env.id,
        browser=browser,
        messages_json=[{"role": "user", "content": user_msg}],
        plan_json={
            "_max_steps": body.max_steps,
            "_headed": bool(body.headed),
            "engine": engine,
            "_auth_mode": session_auth,
            "_account_name": account_name,
            "_execution_mode": execution_mode,
        },
        credentials_json=creds or None,
        events_json=[],
        playwright_code="",
        dry_run_error="",
        error_message="",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    if execution_mode == "agent_once" and body.testcase_id is not None:
        run = create_agent_once_run(
            db,
            project_id=project_id,
            case_id=body.testcase_id,
            user_id=user.id,
            env_id=env.id,
            session_id=row.id,
            cycle_item_id=cycle_item.id if cycle_item is not None else None,
        )
        plan = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
        plan["_case_run_id"] = run.id
        row.plan_json = plan
        db.commit()
        db.refresh(row)

    _start_explore(db, row, env.queue_name or "ui")
    return _session_out(row)


@router.get("/sessions/{session_id}", response_model=UiScenarioAgentSessionOut)
def get_session(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    user: User = Depends(get_current_user),
) -> UiScenarioAgentSessionOut:
    return _session_out(_require_session(db, project_id, session_id, user_id=user.id))


@router.get("/sessions/{session_id}/screenshots")
def get_session_screenshot(
    project_id: int,
    session_id: int,
    key: str = Query(..., min_length=1, max_length=1024),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    user: User = Depends(get_current_user),
) -> FileResponse:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    prefix = f"{project_id}/ui-explore/{row.id}/"
    if ".." in key.replace("\\", "/") or not key.startswith(prefix):
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    path: Path = absolute_path_for_storage_key(key)
    if not path.is_file():
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> None:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    get_event_bus().replace(row.thread_id, [])
    db.delete(row)
    db.commit()


@router.post("/sessions/{session_id}/messages", response_model=UiScenarioAgentSessionOut)
def post_message(
    project_id: int,
    session_id: int,
    body: UiScenarioAgentMessage,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiScenarioAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    llm_cfg = get_llm_runtime(db)

    def _enqueue(sid: int, queue: str) -> str | None:
        if _explore_runner_override is not None:
            _explore_runner_override(db, sid)
            return "inline"
        return enqueue_ui_scenario_agent(sid, queue=queue)

    apply_user_message(
        db,
        row,
        body.content.strip(),
        llm_cfg=llm_cfg,
        enqueue_explore=_enqueue,
    )
    db.refresh(row)
    return _session_out(row)


@router.get("/sessions/{session_id}/events")
def session_events(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    thread_id = row.thread_id
    session_pk = row.id

    def gen() -> Iterator[str]:
        idx = 0
        idle_rounds = 0
        while True:
            # Always refresh from DB so Celery worker logs are visible in API process
            sdb = _resolve_db()
            try:
                srow = sdb.get(UiScenarioAgentSession, session_pk)
                db_events = (
                    list(srow.events_json)
                    if srow and isinstance(srow.events_json, list)
                    else []
                )
                live = get_event_bus().snapshot(thread_id)
                events = live if len(live) >= len(db_events) else db_events
                if db_events and len(db_events) > len(live):
                    get_event_bus().replace(thread_id, db_events)
                st = srow.status if srow else "error"
            finally:
                sdb.close()
            while idx < len(events):
                yield _sse(events[idx])
                ev = events[idx]
                idx += 1
                if ev.get("type") in ("error", "done"):
                    return
            if st in ("confirmed", "cancelled", "error", "done", "awaiting_confirm"):
                if st == "awaiting_confirm":
                    idle_rounds += 1
                    if idle_rounds >= 2:
                        return
                    get_event_bus().wait_after(thread_id, idx, timeout=1.0)
                    continue
                return
            get_event_bus().wait_after(thread_id, idx, timeout=1.5)
            idle_rounds += 1
            if idle_rounds > 80:
                yield _sse({"type": "log", "level": "info", "node": "sse", "message": "日志流心跳（仍在等待 Worker）"})
                idle_rounds = 0

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/stop", response_model=UiScenarioAgentSessionOut)
def stop(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiScenarioAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    stop_session(db, row)
    db.refresh(row)
    return _session_out(row)


@router.post("/sessions/{session_id}/confirm", response_model=UiScenarioAgentSessionOut)
def confirm(
    project_id: int,
    session_id: int,
    body: UiScenarioAgentConfirm,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiScenarioAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    confirm_session(
        db,
        row,
        action=body.action,
        name=body.name,
        script=body.script,
        case_status=body.status,
        user_id=user.id,
    )
    db.refresh(row)
    return _session_out(row)


@router.post("/sessions/{session_id}/trial-run", response_model=UiScenarioAgentSessionOut)
def trial_run(
    project_id: int,
    session_id: int,
    body: UiScenarioAgentTrialRun,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiScenarioAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)

    def _enqueue(sid: int, queue: str) -> str | None:
        if _trial_runner_override is not None:
            _trial_runner_override(db, sid)
            return "inline"
        return enqueue_ui_scenario_agent_trial(sid, queue=queue)

    enqueue_session_trial_run(
        db,
        row,
        case_id=body.case_id or "",
        headed=bool(body.headed),
        script=body.script,
        enqueue=_enqueue,
    )
    db.refresh(row)
    return _session_out(row)
