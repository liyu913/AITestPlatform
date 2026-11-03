from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.app_automation.app_automation import AppCase, AppEnvironment, AppRun
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.identity.user import User
from app.schemas.app_automation.app_automation import (
    AppCaseCreate,
    AppCaseOut,
    AppCaseRunRequest,
    AppCaseUpdate,
    AppRunOut,
)
from app.schemas.page import Page
from app.services.app_automation.app_run_executor import execute_app_run
from app.tasks.app_automation.run_app_case import enqueue_run_app_case
from app.user_messages import (
    APP_CASE_NOT_FOUND,
    APP_ENV_NOT_FOUND,
    MANUAL_CASE_NOT_IN_PROJECT,
)

router = APIRouter(prefix="/projects/{project_id}/app-cases", tags=["app-cases"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_case(db: Session, project_id: int, case_id: int) -> AppCase:
    row = db.query(AppCase).filter(AppCase.id == case_id, AppCase.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", APP_CASE_NOT_FOUND, status_code=404)
    return row


def _validate_manual_case(db: Session, project_id: int, manual_case_id: int | None) -> None:
    if manual_case_id is None:
        return
    c = db.get(TestCase, manual_case_id)
    if c is None or c.project_id != project_id:
        raise AppError("bad_request", MANUAL_CASE_NOT_IN_PROJECT, status_code=400)


def _dump_steps(items: list) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if hasattr(it, "model_dump"):
            out.append(it.model_dump(by_alias=True))
        elif isinstance(it, dict):
            out.append(it)
    return out


def _case_out(row: AppCase) -> AppCaseOut:
    tags = row.tags_json if isinstance(row.tags_json, list) else None
    steps = row.steps_json if isinstance(row.steps_json, list) else []
    return AppCaseOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        steps=steps,
        manual_case_id=row.manual_case_id,
        status=row.status,
        tags_json=tags,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _resolve_env(db: Session, project_id: int, env_id: int | None) -> AppEnvironment | None:
    if env_id is not None:
        env = (
            db.query(AppEnvironment)
            .filter(AppEnvironment.id == env_id, AppEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("not_found", APP_ENV_NOT_FOUND, status_code=404)
        return env
    return (
        db.query(AppEnvironment)
        .filter(AppEnvironment.project_id == project_id, AppEnvironment.is_default.is_(True))
        .first()
    )


@router.get("/", response_model=Page[AppCaseOut])
def list_cases(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[AppCaseOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(AppCase).filter(AppCase.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(AppCase.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(AppCase.updated_at.desc(), AppCase.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[AppCaseOut](items=[_case_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=AppCaseOut, status_code=201)
def create_case(
    project_id: int,
    body: AppCaseCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AppCaseOut:
    _validate_manual_case(db, project_id, body.manual_case_id)
    row = AppCase(
        project_id=project_id,
        name=body.name.strip(),
        description=body.description,
        steps_json=_dump_steps(body.steps),
        manual_case_id=body.manual_case_id,
        status=body.status,
        tags_json=body.tags,
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _case_out(row)


@router.get("/{case_id}", response_model=AppCaseOut)
def get_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> AppCaseOut:
    return _case_out(_require_case(db, project_id, case_id))


@router.patch("/{case_id}", response_model=AppCaseOut)
def update_case(
    project_id: int,
    case_id: int,
    body: AppCaseUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppCaseOut:
    row = _require_case(db, project_id, case_id)
    data = body.model_dump(exclude_unset=True, by_alias=True)
    if "manual_case_id" in data:
        _validate_manual_case(db, project_id, data["manual_case_id"])
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    if "steps" in data:
        row.steps_json = _dump_steps(body.steps or [])
    if "manual_case_id" in data:
        row.manual_case_id = data["manual_case_id"]
    if "status" in data and data["status"] is not None:
        row.status = data["status"]
    if "tags" in data:
        row.tags_json = data["tags"]
    db.commit()
    db.refresh(row)
    return _case_out(row)


@router.delete("/{case_id}", status_code=204)
def delete_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_case(db, project_id, case_id)
    db.delete(row)
    db.commit()


@router.post("/{case_id}/run", response_model=AppRunOut, status_code=201)
def run_case(
    project_id: int,
    case_id: int,
    body: AppCaseRunRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AppRunOut:
    case = _require_case(db, project_id, case_id)
    env = _resolve_env(db, project_id, body.env_id)
    platform = (env.platform if env else "android") or "android"
    queue = (env.queue_name if env else "app") or "app"
    run = AppRun(
        project_id=project_id,
        trigger_type="case",
        target_id=case.id,
        env_id=env.id if env else None,
        device_id=body.device_id,
        device_tags_json=list(body.device_tags) if body.device_tags is not None else None,
        platform=platform,
        account_name=body.account_name,
        queue_name=queue,
        status="pending",
        triggered_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if body.sync:
        execute_app_run(db, run.id)
        db.refresh(run)
    else:
        task_id = enqueue_run_app_case(run.id, queue=queue)
        if task_id:
            run.celery_task_id = task_id
            db.commit()
            db.refresh(run)
    return AppRunOut.model_validate(run)
