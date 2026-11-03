from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiEnvironment, UiSchedule, UiSuite
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.ui_automation.ui_automation import UiScheduleCreate, UiScheduleOut, UiScheduleUpdate
from app.services.api_automation.api_schedule_util import next_run_iso
from app.user_messages import (
    UI_ENV_NOT_FOUND,
    UI_SCHEDULE_CRON_INVALID,
    UI_SCHEDULE_NOT_FOUND,
    UI_SUITE_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/ui-schedules", tags=["ui-schedules"])


def _out(row: UiSchedule) -> UiScheduleOut:
    return UiScheduleOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        suite_id=row.suite_id,
        env_id=row.env_id,
        cron_expr=row.cron_expr,
        enabled=bool(row.enabled),
        last_run_at=row.last_run_at,
        next_run_at=row.next_run_at,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require(db: Session, project_id: int, schedule_id: int) -> UiSchedule:
    row = (
        db.query(UiSchedule)
        .filter(UiSchedule.id == schedule_id, UiSchedule.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", UI_SCHEDULE_NOT_FOUND, status_code=404)
    return row


def _validate_refs(db: Session, project_id: int, suite_id: int, env_id: int | None) -> None:
    suite = db.query(UiSuite).filter(UiSuite.id == suite_id, UiSuite.project_id == project_id).first()
    if suite is None:
        raise AppError("bad_request", UI_SUITE_NOT_FOUND, status_code=400)
    if env_id is not None:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("bad_request", UI_ENV_NOT_FOUND, status_code=400)


def _safe_next(cron_expr: str) -> str:
    try:
        return next_run_iso(cron_expr)
    except Exception as ex:  # noqa: BLE001
        raise AppError("bad_request", UI_SCHEDULE_CRON_INVALID, detail=str(ex), status_code=400) from ex


@router.get("/", response_model=Page[UiScheduleOut])
def list_schedules(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[UiScheduleOut]:
    p, ps = max(1, page), min(100, max(1, page_size))
    q = db.query(UiSchedule).filter(UiSchedule.project_id == project_id)
    if search and search.strip():
        q = q.filter(UiSchedule.name.like(f"%{search.strip()}%"))
    total = q.count()
    rows = q.order_by(UiSchedule.updated_at.desc(), UiSchedule.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[UiScheduleOut](items=[_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=UiScheduleOut, status_code=201)
def create_schedule(
    project_id: int,
    body: UiScheduleCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiScheduleOut:
    _validate_refs(db, project_id, body.suite_id, body.env_id)
    nxt = _safe_next(body.cron_expr)
    row = UiSchedule(
        project_id=project_id,
        name=body.name.strip(),
        suite_id=body.suite_id,
        env_id=body.env_id,
        cron_expr=body.cron_expr.strip(),
        enabled=body.enabled,
        next_run_at=nxt if body.enabled else None,
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch("/{schedule_id}", response_model=UiScheduleOut)
def update_schedule(
    project_id: int,
    schedule_id: int,
    body: UiScheduleUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiScheduleOut:
    row = _require(db, project_id, schedule_id)
    data = body.model_dump(exclude_unset=True)
    suite_id = data.get("suite_id", row.suite_id)
    env_id = data.get("env_id", row.env_id) if "env_id" in data else row.env_id
    if "suite_id" in data or "env_id" in data:
        _validate_refs(db, project_id, suite_id, env_id)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "suite_id" in data and data["suite_id"] is not None:
        row.suite_id = data["suite_id"]
    if "env_id" in data:
        row.env_id = data["env_id"]
    if "cron_expr" in data and data["cron_expr"] is not None:
        row.cron_expr = str(data["cron_expr"]).strip()
        row.next_run_at = _safe_next(row.cron_expr)
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
        row.next_run_at = _safe_next(row.cron_expr) if row.enabled else None
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(
    project_id: int,
    schedule_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require(db, project_id, schedule_id)
    db.delete(row)
    db.commit()
