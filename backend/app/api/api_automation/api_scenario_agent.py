"""API routes for LangGraph scenario agent (definitions + multi-turn + SSE)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.deps import SessionLocal, get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiScenarioAgentSession
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.api_automation.api_scenario_agent import (
    ScenarioAgentConfirm,
    ScenarioAgentMessage,
    ScenarioAgentSessionCreate,
    ScenarioAgentSessionOut,
    ScenarioAgentSessionSummary,
)
from app.services.api_automation.api_scenario_events import get_event_bus
from app.services.api_automation.api_scenario_graph import (
    get_compiled_graph,
    load_candidate_definitions,
    new_thread_id,
    reset_compiled_graph,
    resume_after_confirm,
    run_until_interrupt,
    sync_session_from_state,
)
from app.services.ai.effective_ai_settings import get_llm_runtime

router = APIRouter(prefix="/projects/{project_id}/api-scenario-agent", tags=["api-scenario-agent"])

_db_factory_override = None


def set_db_factory(factory) -> None:
    global _db_factory_override
    _db_factory_override = factory
    reset_compiled_graph()
    get_compiled_graph(db_factory=_resolve_db_factory)


def _resolve_db_factory() -> Session:
    if _db_factory_override is not None:
        return _db_factory_override()
    return SessionLocal()


get_compiled_graph(db_factory=_resolve_db_factory)


def _session_out(row: ApiScenarioAgentSession) -> ScenarioAgentSessionOut:
    def_ids = (
        row.candidate_definition_ids_json
        if isinstance(row.candidate_definition_ids_json, list)
        else []
    )
    case_ids = row.candidate_case_ids_json if isinstance(row.candidate_case_ids_json, list) else []
    messages = row.messages_json if isinstance(row.messages_json, list) else []
    events = row.events_json if isinstance(row.events_json, list) else []
    live = get_event_bus().snapshot(row.thread_id)
    if live:
        events = live
    return ScenarioAgentSessionOut(
        id=row.id,
        project_id=row.project_id,
        user_id=row.user_id,
        thread_id=row.thread_id,
        status=row.status,
        prompt=row.prompt or "",
        candidate_definition_ids=[int(x) for x in def_ids],
        candidate_case_ids=[int(x) for x in case_ids],
        messages=[{"role": str(m.get("role", "")), "content": str(m.get("content", ""))} for m in messages if isinstance(m, dict)],
        plan=row.plan_json if isinstance(row.plan_json, dict) else None,
        events=events,
        suite_id=row.suite_id,
        run_id=row.run_id,
        error_message=row.error_message or "",
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
) -> ApiScenarioAgentSession:
    q = db.query(ApiScenarioAgentSession).filter(
        ApiScenarioAgentSession.id == session_id,
        ApiScenarioAgentSession.project_id == project_id,
    )
    if user_id is not None:
        q = q.filter(ApiScenarioAgentSession.user_id == user_id)
    row = q.first()
    if row is None:
        raise AppError("not_found", "场景会话不存在", status_code=404)
    return row


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _apply_graph_result(db: Session, row: ApiScenarioAgentSession, result: dict) -> None:
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else row.plan_json
    messages = result.get("messages") if isinstance(result.get("messages"), list) else None
    err = result.get("error")
    interrupted = bool(result.get("__interrupt__"))
    decision = result.get("user_decision")

    if interrupted:
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            suite_id=None,
            run_id=None,
            status="awaiting_confirm",
            messages=messages,
        )
    elif err and not interrupted and decision not in ("confirm", "cancel"):
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            suite_id=result.get("suite_id"),
            run_id=result.get("run_id"),
            status="error",
            error=str(err),
            messages=messages,
        )
    elif decision == "cancel":
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            suite_id=None,
            run_id=None,
            status="cancelled",
            messages=messages,
        )
    elif decision == "confirm":
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            suite_id=result.get("suite_id"),
            run_id=result.get("run_id"),
            status="error" if err else "confirmed",
            error=str(err) if err else None,
            messages=messages,
        )
    else:
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            suite_id=result.get("suite_id"),
            run_id=result.get("run_id"),
            status="done",
            messages=messages,
        )


@router.get("/sessions", response_model=list[ScenarioAgentSessionSummary])
def list_sessions(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[ScenarioAgentSessionSummary]:
    rows = (
        db.query(ApiScenarioAgentSession)
        .filter(
            ApiScenarioAgentSession.project_id == project_id,
            ApiScenarioAgentSession.user_id == user.id,
        )
        .order_by(ApiScenarioAgentSession.updated_at.desc())
        .all()
    )
    return [
        ScenarioAgentSessionSummary(
            id=r.id,
            title=_session_title(r.prompt or ""),
            status=r.status or "running",
            updated_at=r.updated_at,
            message_count=len(r.messages_json) if isinstance(r.messages_json, list) else 0,
        )
        for r in rows
    ]


@router.post("/sessions", response_model=ScenarioAgentSessionOut, status_code=201)
def create_session(
    project_id: int,
    body: ScenarioAgentSessionCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ScenarioAgentSessionOut:
    llm_cfg = get_llm_runtime(db)
    if not llm_cfg.api_key.strip():
        raise AppError("bad_request", "未配置 LLM API Key", status_code=400)

    candidates = load_candidate_definitions(db, project_id, body.definition_ids)
    if len(candidates) != len(body.definition_ids):
        raise AppError("bad_request", "部分接口不属于当前项目", status_code=400)

    thread_id = new_thread_id()
    get_event_bus().replace(thread_id, [])
    row = ApiScenarioAgentSession(
        project_id=project_id,
        user_id=user.id,
        thread_id=thread_id,
        status="running",
        prompt=(body.prompt or "").strip(),
        candidate_definition_ids_json=body.definition_ids,
        candidate_case_ids_json=[],
        messages_json=[],
        plan_json=None,
        events_json=[],
        error_message="",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    result = run_until_interrupt(
        db_factory=_resolve_db_factory,
        thread_id=thread_id,
        project_id=project_id,
        user_id=user.id,
        prompt=(body.prompt or "").strip(),
        candidate_definitions=candidates,
        llm_cfg=llm_cfg,
    )
    _apply_graph_result(db, row, result)
    db.refresh(row)
    return _session_out(row)


@router.get("/sessions/{session_id}", response_model=ScenarioAgentSessionOut)
def get_session(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    user: User = Depends(get_current_user),
) -> ScenarioAgentSessionOut:
    return _session_out(_require_session(db, project_id, session_id, user_id=user.id))


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


@router.post("/sessions/{session_id}/messages", response_model=ScenarioAgentSessionOut)
def post_message(
    project_id: int,
    session_id: int,
    body: ScenarioAgentMessage,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ScenarioAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    if row.status != "awaiting_confirm":
        raise AppError("bad_request", f"当前状态不可继续对话: {row.status}", status_code=400)

    # keep LLM bound for revise loop
    from app.services.api_automation.api_scenario_graph import bind_llm

    llm_cfg = get_llm_runtime(db)
    bind_llm(row.thread_id, llm_cfg)

    row.status = "running"
    db.commit()

    result = resume_after_confirm(
        db_factory=_resolve_db_factory,
        thread_id=row.thread_id,
        action="revise",
        message=body.content.strip(),
        unbind=False,
    )
    _apply_graph_result(db, row, result)
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
            events = get_event_bus().snapshot(thread_id)
            if not events:
                sdb = SessionLocal()
                try:
                    srow = sdb.get(ApiScenarioAgentSession, session_pk)
                    if srow and isinstance(srow.events_json, list) and srow.events_json:
                        get_event_bus().replace(thread_id, list(srow.events_json))
                        events = get_event_bus().snapshot(thread_id)
                finally:
                    sdb.close()
            while idx < len(events):
                yield _sse(events[idx])
                ev = events[idx]
                idx += 1
                if ev.get("type") == "error":
                    return
                if ev.get("type") == "done":
                    return
            sdb = SessionLocal()
            try:
                srow = sdb.get(ApiScenarioAgentSession, session_pk)
                st = srow.status if srow else "error"
            finally:
                sdb.close()
            if st in ("confirmed", "cancelled", "error", "done"):
                events = get_event_bus().snapshot(thread_id)
                while idx < len(events):
                    yield _sse(events[idx])
                    idx += 1
                return
            if st == "awaiting_confirm" and idx > 0:
                idle_rounds += 1
                if idle_rounds >= 2:
                    return
            get_event_bus().wait_after(thread_id, idx, timeout=2.0)
            idle_rounds += 1
            if idle_rounds > 30:
                yield _sse({"type": "log", "level": "info", "node": "sse", "message": "日志流心跳"})
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


@router.post("/sessions/{session_id}/confirm", response_model=ScenarioAgentSessionOut)
def confirm_session(
    project_id: int,
    session_id: int,
    body: ScenarioAgentConfirm,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ScenarioAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    if row.status != "awaiting_confirm":
        raise AppError("bad_request", f"会话状态不可确认: {row.status}", status_code=400)

    from app.services.api_automation.api_scenario_graph import bind_llm

    llm_cfg = get_llm_runtime(db)
    bind_llm(row.thread_id, llm_cfg)

    row.status = "running"
    db.commit()

    result = resume_after_confirm(
        db_factory=_resolve_db_factory,
        thread_id=row.thread_id,
        action=body.action,
        unbind=True,
    )
    _apply_graph_result(db, row, result)
    db.refresh(row)
    return _session_out(row)
