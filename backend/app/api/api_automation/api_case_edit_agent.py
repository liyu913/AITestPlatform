"""API routes for LangGraph batch ApiCase edit agent (multi-turn + SSE)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.deps import SessionLocal, get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase, ApiCaseEditAgentSession
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.schemas.api_automation.api_case_edit_agent import (
    CaseEditAgentConfirm,
    CaseEditAgentMessage,
    CaseEditAgentSessionCreate,
    CaseEditAgentSessionOut,
    CaseEditAgentSessionSummary,
)
from app.services.ai.effective_ai_settings import get_llm_runtime
from app.services.api_automation.api_case_edit_agent_graph import (
    MAX_CASES,
    get_compiled_graph,
    new_thread_id,
    reset_compiled_graph,
    resume_after_confirm,
    run_until_interrupt,
    sync_session_from_state,
)
from app.services.api_automation.api_scenario_events import get_event_bus

router = APIRouter(
    prefix="/projects/{project_id}/api-case-edit-agent",
    tags=["api-case-edit-agent"],
)

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


def _session_out(row: ApiCaseEditAgentSession) -> CaseEditAgentSessionOut:
    case_ids = row.case_ids_json if isinstance(row.case_ids_json, list) else []
    applied = row.applied_case_ids_json if isinstance(row.applied_case_ids_json, list) else []
    messages = row.messages_json if isinstance(row.messages_json, list) else []
    events = row.events_json if isinstance(row.events_json, list) else []
    snapshots = row.base_snapshots_json if isinstance(row.base_snapshots_json, list) else []
    live = get_event_bus().snapshot(row.thread_id)
    if live:
        events = live
    return CaseEditAgentSessionOut(
        id=row.id,
        project_id=row.project_id,
        user_id=row.user_id,
        thread_id=row.thread_id,
        status=row.status,
        prompt=row.prompt or "",
        case_ids=[int(x) for x in case_ids],
        base_snapshots=[s for s in snapshots if isinstance(s, dict)],
        messages=[
            {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
            for m in messages
            if isinstance(m, dict)
        ],
        plan=row.plan_json if isinstance(row.plan_json, dict) else None,
        events=events,
        applied_case_ids=[int(x) for x in applied],
        error_message=row.error_message or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _session_title(prompt: str, case_ids: list[int]) -> str:
    text = (prompt or "").strip()
    if text:
        return text[:40] + ("…" if len(text) > 40 else "")
    n = len(case_ids)
    return f"批量修订 {n} 条用例" if n else "新修订会话"


def _require_session(
    db: Session,
    project_id: int,
    session_id: int,
    user_id: int | None = None,
) -> ApiCaseEditAgentSession:
    q = db.query(ApiCaseEditAgentSession).filter(
        ApiCaseEditAgentSession.id == session_id,
        ApiCaseEditAgentSession.project_id == project_id,
    )
    if user_id is not None:
        q = q.filter(ApiCaseEditAgentSession.user_id == user_id)
    row = q.first()
    if row is None:
        raise AppError("not_found", "用例修订会话不存在", status_code=404)
    return row


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _apply_graph_result(db: Session, row: ApiCaseEditAgentSession, result: dict) -> None:
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else row.plan_json
    messages = result.get("messages") if isinstance(result.get("messages"), list) else None
    err = result.get("error")
    interrupted = bool(result.get("__interrupt__"))
    decision = result.get("user_decision")
    applied = result.get("applied_case_ids")
    if not isinstance(applied, list):
        applied = row.applied_case_ids_json if isinstance(row.applied_case_ids_json, list) else None
    snapshots = result.get("base_snapshots")
    if not isinstance(snapshots, list):
        snapshots = None

    if interrupted:
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            applied_case_ids=None,
            status="awaiting_confirm",
            messages=messages,
            base_snapshots=snapshots,
        )
    elif err and not interrupted and decision not in ("confirm", "cancel"):
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            applied_case_ids=applied,
            status="error",
            error=str(err),
            messages=messages,
            base_snapshots=snapshots,
        )
    elif decision == "cancel":
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            applied_case_ids=None,
            status="cancelled",
            messages=messages,
            base_snapshots=snapshots,
        )
    elif decision == "confirm":
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            applied_case_ids=applied,
            status="error" if err else "confirmed",
            error=str(err) if err else None,
            messages=messages,
            base_snapshots=snapshots,
        )
    else:
        sync_session_from_state(
            db,
            row,
            plan=plan if isinstance(plan, dict) else None,
            applied_case_ids=applied,
            status="done",
            messages=messages,
            base_snapshots=snapshots,
        )


@router.get("/sessions", response_model=list[CaseEditAgentSessionSummary])
def list_sessions(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[CaseEditAgentSessionSummary]:
    rows = (
        db.query(ApiCaseEditAgentSession)
        .filter(
            ApiCaseEditAgentSession.project_id == project_id,
            ApiCaseEditAgentSession.user_id == user.id,
        )
        .order_by(ApiCaseEditAgentSession.updated_at.desc())
        .all()
    )
    out: list[CaseEditAgentSessionSummary] = []
    for r in rows:
        case_ids = r.case_ids_json if isinstance(r.case_ids_json, list) else []
        out.append(
            CaseEditAgentSessionSummary(
                id=r.id,
                title=_session_title(r.prompt or "", [int(x) for x in case_ids]),
                status=r.status or "running",
                updated_at=r.updated_at,
                message_count=len(r.messages_json) if isinstance(r.messages_json, list) else 0,
                case_count=len(case_ids),
            )
        )
    return out


@router.post("/sessions", response_model=CaseEditAgentSessionOut, status_code=201)
def create_session(
    project_id: int,
    body: CaseEditAgentSessionCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> CaseEditAgentSessionOut:
    llm_cfg = get_llm_runtime(db)
    if not llm_cfg.api_key.strip():
        raise AppError("bad_request", "未配置 LLM API Key", status_code=400)

    case_ids = body.case_ids
    if len(case_ids) > MAX_CASES:
        raise AppError("bad_request", f"一次最多修订 {MAX_CASES} 条用例", status_code=400)

    found = (
        db.query(ApiCase.id)
        .filter(ApiCase.project_id == project_id, ApiCase.id.in_(case_ids))
        .all()
    )
    found_ids = {int(r[0]) for r in found}
    if len(found_ids) != len(case_ids):
        missing = [c for c in case_ids if c not in found_ids]
        raise AppError("bad_request", f"部分用例不属于当前项目: {missing}", status_code=400)

    thread_id = new_thread_id()
    get_event_bus().replace(thread_id, [])
    row = ApiCaseEditAgentSession(
        project_id=project_id,
        user_id=user.id,
        thread_id=thread_id,
        status="running",
        prompt=(body.prompt or "").strip(),
        case_ids_json=case_ids,
        base_snapshots_json=None,
        messages_json=[],
        plan_json=None,
        events_json=[],
        applied_case_ids_json=None,
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
        case_ids=case_ids,
        llm_cfg=llm_cfg,
    )
    _apply_graph_result(db, row, result)
    db.refresh(row)
    return _session_out(row)


@router.get("/sessions/{session_id}", response_model=CaseEditAgentSessionOut)
def get_session(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    user: User = Depends(get_current_user),
) -> CaseEditAgentSessionOut:
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


@router.post("/sessions/{session_id}/messages", response_model=CaseEditAgentSessionOut)
def post_message(
    project_id: int,
    session_id: int,
    body: CaseEditAgentMessage,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> CaseEditAgentSessionOut:
    row = _require_session(db, project_id, session_id, user_id=user.id)
    if row.status != "awaiting_confirm":
        raise AppError("bad_request", f"当前状态不可继续对话: {row.status}", status_code=400)

    from app.services.api_automation.api_case_edit_agent_graph import bind_llm

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
                    srow = sdb.get(ApiCaseEditAgentSession, session_pk)
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
                srow = sdb.get(ApiCaseEditAgentSession, session_pk)
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


def _best_effort_clear_interrupt(thread_id: str, *, action: str, case_ids: list[int] | None = None) -> None:
    """Resume graph only when an in-memory interrupt still exists; never start a new run."""
    from app.services.api_automation.api_case_edit_agent_graph import (
        get_compiled_graph,
        unbind_llm,
    )

    try:
        graph = get_compiled_graph(db_factory=_resolve_db_factory)
        config = {"configurable": {"thread_id": thread_id}}
        snap = graph.get_state(config)
        if not snap or not getattr(snap, "next", None):
            unbind_llm(thread_id)
            return
        resume_after_confirm(
            db_factory=_resolve_db_factory,
            thread_id=thread_id,
            action=action,
            case_ids=case_ids,
            unbind=True,
        )
    except Exception:  # noqa: BLE001
        unbind_llm(thread_id)


@router.post("/sessions/{session_id}/confirm", response_model=CaseEditAgentSessionOut)
def confirm_session(
    project_id: int,
    session_id: int,
    body: CaseEditAgentConfirm,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> CaseEditAgentSessionOut:
    """Confirm/cancel using persisted plan_json — does not depend on MemorySaver."""
    from app.services.api_automation.api_case_edit_agent_graph import apply_revisions_from_plan

    row = _require_session(db, project_id, session_id, user_id=user.id)
    if row.status != "awaiting_confirm":
        raise AppError("bad_request", f"会话状态不可确认: {row.status}", status_code=400)

    plan = row.plan_json if isinstance(row.plan_json, dict) else None
    thread_id = row.thread_id

    if body.action == "cancel":
        row.status = "cancelled"
        row.applied_case_ids_json = []
        row.error_message = ""
        db.commit()
        _best_effort_clear_interrupt(thread_id, action="cancel")
        get_event_bus().append(thread_id, {"type": "done", "status": "cancelled", "applied_case_ids": []})
        row.events_json = get_event_bus().snapshot(thread_id)
        db.add(row)
        db.commit()
        db.refresh(row)
        return _session_out(row)

    # confirm: apply from durable plan_json
    if not plan or not isinstance(plan.get("revisions"), list) or not plan.get("revisions"):
        raise AppError("bad_request", "当前会话没有可应用的修订草稿", status_code=400)

    applied = apply_revisions_from_plan(db, project_id, plan, body.case_ids)
    row.status = "confirmed"
    row.applied_case_ids_json = applied
    row.error_message = ""
    db.commit()

    _best_effort_clear_interrupt(thread_id, action="confirm", case_ids=body.case_ids)

    get_event_bus().append(
        thread_id,
        {"type": "done", "status": "confirmed", "applied_case_ids": applied},
    )
    row.events_json = get_event_bus().snapshot(thread_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _session_out(row)
