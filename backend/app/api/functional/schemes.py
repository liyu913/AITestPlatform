from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.scheme import TestScheme, TestSchemeVersion
from app.models.functional.test_plan import TestPlan
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.schemas.functional.scheme import (
    SchemeCreate,
    SchemeDetailOut,
    SchemeOut,
    SchemeUpdate,
    SchemeVersionCreate,
    SchemeVersionOut,
)
from app.schemas.page import Page
from app.services.functional import scheme_service

router = APIRouter(prefix="/projects/{project_id}/schemes", tags=["schemes"])


def _latest(scheme: TestScheme) -> TestSchemeVersion | None:
    return max(scheme.versions, key=lambda v: v.version_no, default=None)


def _to_out(db: Session, scheme: TestScheme) -> SchemeOut:
    latest = _latest(scheme)
    plan_count = (
        db.query(TestPlan).filter(TestPlan.scheme_id == scheme.id).count()
    )
    return SchemeOut(
        id=scheme.id,
        project_id=scheme.project_id,
        name=scheme.name,
        status=scheme.status,
        owner_id=scheme.owner_id,
        current_version_no=scheme.current_version_no,
        plan_count=plan_count,
        latest_snapshot=latest.snapshot if latest else None,
        created_at=scheme.created_at,
        updated_at=scheme.updated_at,
    )


def _to_detail(db: Session, scheme: TestScheme) -> SchemeDetailOut:
    base = _to_out(db, scheme)
    return SchemeDetailOut(
        **base.model_dump(),
        versions=[SchemeVersionOut.model_validate(v) for v in scheme.versions],
    )


def _require_scheme(db: Session, project_id: int, scheme_id: int) -> TestScheme:
    scheme = (
        db.query(TestScheme)
        .options(selectinload(TestScheme.versions))
        .filter(TestScheme.id == scheme_id, TestScheme.project_id == project_id)
        .first()
    )
    if scheme is None:
        raise AppError("not_found", "测试方案不存在", status_code=404)
    return scheme


@router.get("/", response_model=Page[SchemeOut])
def list_schemes(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, max_length=32),
    search: str | None = Query(None, max_length=256),
) -> Page[SchemeOut]:
    filtered = db.query(TestScheme).filter(TestScheme.project_id == project_id)
    if status:
        filtered = filtered.filter(TestScheme.status == status)
    if search and search.strip():
        filtered = filtered.filter(TestScheme.name.contains(search.strip()))
    total = filtered.count()
    rows = (
        filtered.options(selectinload(TestScheme.versions))
        .order_by(TestScheme.updated_at.desc(), TestScheme.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(items=[_to_out(db, s) for s in rows], total=total, page=page, page_size=page_size)


@router.post("/", response_model=SchemeDetailOut, status_code=201)
def create_scheme(
    project_id: int,
    payload: SchemeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> SchemeDetailOut:
    scheme = scheme_service.create_scheme(
        db, project_id=project_id, payload=payload, user_id=user.id
    )
    return _to_detail(db, _require_scheme(db, project_id, scheme.id))


@router.get("/{scheme_id}", response_model=SchemeDetailOut)
def get_scheme(
    project_id: int,
    scheme_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> SchemeDetailOut:
    return _to_detail(db, _require_scheme(db, project_id, scheme_id))


@router.patch("/{scheme_id}", response_model=SchemeDetailOut)
def update_scheme(
    project_id: int,
    scheme_id: int,
    payload: SchemeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> SchemeDetailOut:
    scheme = _require_scheme(db, project_id, scheme_id)
    scheme_service.update_scheme(db, scheme=scheme, payload=payload, user_id=user.id)
    return _to_detail(db, _require_scheme(db, project_id, scheme_id))


@router.delete("/{scheme_id}", status_code=204)
def delete_scheme(
    project_id: int,
    scheme_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> None:
    scheme = _require_scheme(db, project_id, scheme_id)
    db.delete(scheme)
    db.commit()


@router.get("/{scheme_id}/versions", response_model=list[SchemeVersionOut])
def list_scheme_versions(
    project_id: int,
    scheme_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[SchemeVersionOut]:
    scheme = _require_scheme(db, project_id, scheme_id)
    return [SchemeVersionOut.model_validate(v) for v in reversed(scheme.versions)]


@router.post("/{scheme_id}/versions", response_model=SchemeVersionOut, status_code=201)
def create_scheme_version(
    project_id: int,
    scheme_id: int,
    payload: SchemeVersionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> SchemeVersionOut:
    scheme = _require_scheme(db, project_id, scheme_id)
    version = scheme_service.add_scheme_version(
        db, scheme=scheme, user_id=user.id, change_note=payload.change_note
    )
    return SchemeVersionOut.model_validate(version)
