"""Test scheme versioning.

A scheme is edited as a whole, but every content change produces a new
immutable version snapshot. Plans reference a specific scheme version, so a
later edit cannot change what an existing plan was approved against.
"""

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.functional.scheme import TestScheme, TestSchemeVersion
from app.schemas.functional.scheme import (
    SCHEME_CONTENT_FIELDS,
    SCHEME_STATUSES,
    SchemeCreate,
    SchemeUpdate,
)


def normalize_status(value: str | None, *, default: str = "draft") -> str:
    cleaned = (value or "").strip()
    return cleaned if cleaned in SCHEME_STATUSES else default


def build_snapshot(scheme: TestScheme) -> dict:
    snapshot: dict = {"name": scheme.name}
    for field in SCHEME_CONTENT_FIELDS[1:]:
        snapshot[field] = getattr(scheme, field, "") or ""
    return snapshot


def _apply(scheme: TestScheme, payload: SchemeUpdate) -> bool:
    changed = False
    for field in SCHEME_CONTENT_FIELDS:
        value = getattr(payload, field, None)
        if value is None:
            continue
        if getattr(scheme, field, None) != value:
            setattr(scheme, field, value)
            changed = True
    if payload.status is not None:
        scheme.status = normalize_status(payload.status, default=scheme.status)
    if payload.owner_id is not None:
        scheme.owner_id = payload.owner_id
    return changed


def _add_version(
    db: Session, scheme: TestScheme, *, user_id: int | None, change_note: str | None
) -> TestSchemeVersion:
    scheme.current_version_no = int(scheme.current_version_no or 0) + 1
    version = TestSchemeVersion(
        scheme_id=scheme.id,
        project_id=scheme.project_id,
        version_no=scheme.current_version_no,
        snapshot=build_snapshot(scheme),
        change_note=change_note,
        created_by_id=user_id,
    )
    db.add(version)
    return version


def create_scheme(
    db: Session, *, project_id: int, payload: SchemeCreate, user_id: int | None
) -> TestScheme:
    scheme = TestScheme(
        project_id=project_id,
        name=payload.name.strip(),
        status=normalize_status(payload.status),
        owner_id=payload.owner_id,
        current_version_no=0,
        creator_id=user_id,
    )
    for field in SCHEME_CONTENT_FIELDS[1:]:
        setattr(scheme, field, getattr(payload, field, "") or "")
    db.add(scheme)
    db.flush()
    version = _add_version(db, scheme, user_id=user_id, change_note="创建方案")
    db.flush()
    scheme.current_version_no = version.version_no
    db.commit()
    db.refresh(scheme)
    return scheme


def update_scheme(
    db: Session, *, scheme: TestScheme, payload: SchemeUpdate, user_id: int | None
) -> TestScheme:
    changed = _apply(scheme, payload)
    if changed:
        _add_version(
            db,
            scheme,
            user_id=user_id,
            change_note=(payload.change_note or "编辑方案").strip() or "编辑方案",
        )
    db.commit()
    db.refresh(scheme)
    return scheme


def add_scheme_version(
    db: Session, *, scheme: TestScheme, user_id: int | None, change_note: str | None
) -> TestSchemeVersion:
    version = _add_version(db, scheme, user_id=user_id, change_note=change_note or "手动留档")
    db.commit()
    db.refresh(version)
    return version


def require_scheme_version(db: Session, *, project_id: int, version_id: int) -> TestSchemeVersion:
    version = (
        db.query(TestSchemeVersion)
        .filter(
            TestSchemeVersion.id == version_id,
            TestSchemeVersion.project_id == project_id,
        )
        .first()
    )
    if version is None:
        raise AppError("not_found", "方案版本不存在", status_code=404)
    return version
