from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_superuser
from app.errors import AppError
from app.models.ai.ai_call_log import AiCallLog
from app.models.identity.user import User
from app.models.project.project import Project
from app.schemas.ai.ai_call_log import AiCallLogDetail, AiCallLogListItem
from app.schemas.page import Page

router = APIRouter(prefix="/admin/ai-call-logs", tags=["admin"])


def _item(row: AiCallLog, username: str | None, project_name: str | None) -> AiCallLogListItem:
    return AiCallLogListItem(
        id=row.id,
        created_at=row.created_at,
        kind=row.kind,
        scene=row.scene,
        model=row.model,
        status=row.status,
        http_status=row.http_status,
        latency_ms=row.latency_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        user_id=row.user_id,
        username=username,
        project_id=row.project_id,
        project_name=project_name,
        request_id=row.request_id,
    )


def _detail(row: AiCallLog, username: str | None, project_name: str | None) -> AiCallLogDetail:
    base = _item(row, username, project_name)
    return AiCallLogDetail(
        **base.model_dump(),
        error_message=row.error_message,
        request_text=row.request_text,
        response_text=row.response_text,
    )


@router.get("", response_model=Page[AiCallLogListItem])
def list_ai_call_logs(
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    scene: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[AiCallLogListItem]:
    q = (
        db.query(AiCallLog, User.username, Project.name)
        .outerjoin(User, User.id == AiCallLog.user_id)
        .outerjoin(Project, Project.id == AiCallLog.project_id)
    )
    if kind:
        q = q.filter(AiCallLog.kind == kind)
    if status:
        q = q.filter(AiCallLog.status == status)
    scene_text = (scene or "").strip()
    if scene_text:
        q = q.filter(AiCallLog.scene.contains(scene_text))
    total = q.count()
    rows = (
        q.order_by(AiCallLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(
        items=[_item(row, username, project_name) for row, username, project_name in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{log_id}", response_model=AiCallLogDetail)
def get_ai_call_log(
    log_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> AiCallLogDetail:
    row = db.get(AiCallLog, log_id)
    if row is None:
        raise AppError("not_found", "AI 调用日志不存在", status_code=404)
    user = db.get(User, row.user_id) if row.user_id else None
    project = db.get(Project, row.project_id) if row.project_id else None
    return _detail(
        row,
        user.username if user else None,
        project.name if project else None,
    )
