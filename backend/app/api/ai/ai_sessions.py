import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.user_messages import AI_SESSION_NOT_FOUND
from app.models.ai.ai_session import DEFAULT_SESSION_TITLE, AiGenerationSession
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.ai.ai_gen import AiSessionOut, AiSessionSummary, AiSessionUpsert, AgentArtifacts, ChatMessage, GeneratedCase

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/ai", tags=["ai"])


def _session_for_user(
    db: Session,
    project_id: int,
    session_id: int,
    user_id: int,
) -> AiGenerationSession:
    row = (
        db.query(AiGenerationSession)
        .filter(
            AiGenerationSession.id == session_id,
            AiGenerationSession.project_id == project_id,
            AiGenerationSession.user_id == user_id,
        )
        .first()
    )
    if row is None:
        raise AppError("not_found", AI_SESSION_NOT_FOUND, status_code=404)
    return row


def _session_to_out(row: AiGenerationSession) -> AiSessionOut:
    messages = [ChatMessage.model_validate(m) for m in (row.messages_json or [])]
    drafts = [GeneratedCase.model_validate(d) for d in (row.drafts_json or [])]
    artifacts = (
        AgentArtifacts.model_validate(row.artifacts_json)
        if row.artifacts_json
        else None
    )
    mode = (row.generate_mode or "fast").strip() or "fast"
    if mode not in ("fast", "agent"):
        mode = "fast"
    return AiSessionOut(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        messages=messages,
        drafts=drafts,
        requirement_id=row.requirement_id,
        knowledge_library_id=row.knowledge_library_id,
        llm_model=row.llm_model,
        generate_mode=mode,  # type: ignore[arg-type]
        artifacts=artifacts,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _auto_title_from_messages(messages: list) -> str | None:
    for m in messages:
        role = m.role if hasattr(m, "role") else (m.get("role") if isinstance(m, dict) else None)
        content = m.content if hasattr(m, "content") else (m.get("content") if isinstance(m, dict) else None)
        if role == "user" and isinstance(content, str) and content.strip():
            text = content.strip()
            return text[:40] + ("…" if len(text) > 40 else "")
    return None


def _apply_upsert(row: AiGenerationSession, body: AiSessionUpsert) -> None:
    if body.title is not None and body.title.strip():
        row.title = body.title.strip()[:255]
    elif (row.title or DEFAULT_SESSION_TITLE) == DEFAULT_SESSION_TITLE:
        auto = _auto_title_from_messages(body.messages)
        if auto:
            row.title = auto
    row.messages_json = [m.model_dump() for m in body.messages]
    row.drafts_json = [d.model_dump() for d in body.drafts]
    row.requirement_id = body.requirement_id
    row.knowledge_library_id = body.knowledge_library_id
    row.llm_model = (body.llm_model or "").strip() or None
    row.generate_mode = body.generate_mode if body.generate_mode in ("fast", "agent") else "fast"
    row.artifacts_json = body.artifacts.model_dump() if body.artifacts else None


@router.get("/sessions", response_model=list[AiSessionSummary])
def list_sessions(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[AiSessionSummary]:
    rows = (
        db.query(AiGenerationSession)
        .filter(
            AiGenerationSession.project_id == project_id,
            AiGenerationSession.user_id == user.id,
        )
        .order_by(AiGenerationSession.updated_at.desc())
        .all()
    )
    return [
        AiSessionSummary(
            id=r.id,
            title=r.title,
            updated_at=r.updated_at,
            message_count=len(r.messages_json or []),
            draft_count=len(r.drafts_json or []),
        )
        for r in rows
    ]


@router.post("/sessions", response_model=AiSessionOut, status_code=201)
def create_session(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AiSessionOut:
    row = AiGenerationSession(
        project_id=project_id,
        user_id=user.id,
        title=DEFAULT_SESSION_TITLE,
        messages_json=[],
        drafts_json=[],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("ai session created project_id=%s user_id=%s session_id=%s", project_id, user.id, row.id)
    return _session_to_out(row)


@router.get("/sessions/{session_id}", response_model=AiSessionOut)
def get_session(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AiSessionOut:
    row = _session_for_user(db, project_id, session_id, user.id)
    return _session_to_out(row)


@router.put("/sessions/{session_id}", response_model=AiSessionOut)
def update_session(
    project_id: int,
    session_id: int,
    body: AiSessionUpsert,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AiSessionOut:
    row = _session_for_user(db, project_id, session_id, user.id)
    _apply_upsert(row, body)
    db.commit()
    db.refresh(row)
    return _session_to_out(row)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    project_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> None:
    row = _session_for_user(db, project_id, session_id, user.id)
    db.delete(row)
    db.commit()
    log.info("ai session deleted project_id=%s user_id=%s session_id=%s", project_id, user.id, session_id)
