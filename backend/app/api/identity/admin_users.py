from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_superuser
from app.errors import AppError
from app.models.identity.user import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    User,
)
from app.schemas.identity.user import AdminResetPasswordRequest, UserAdminListOut, UserAdminPatch, UserOut
from app.services.identity import auth_service
from app.user_messages import CANNOT_RESET_SUPERUSER_PASSWORD, USER_NOT_FOUND

router = APIRouter(prefix="/admin/users", tags=["admin"])


def _user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("not_found", USER_NOT_FOUND, status_code=404)
    return user


@router.get("", response_model=UserAdminListOut)
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
    status: str | None = Query(default=None, description="pending|approved|rejected"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> UserAdminListOut:
    q = db.query(User)
    if status in (APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED):
        q = q.filter(User.approval_status == status)
    total = q.count()
    rows = (
        q.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return UserAdminListOut(
        items=[UserOut.model_validate(u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/{user_id}/approve", response_model=UserOut)
def approve_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> UserOut:
    user = _user_or_404(db, user_id)
    user.approval_status = APPROVAL_APPROVED
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/reject", response_model=UserOut)
def reject_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> UserOut:
    user = _user_or_404(db, user_id)
    user.approval_status = APPROVAL_REJECTED
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: UserAdminPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> UserOut:
    user = _user_or_404(db, user_id)
    if user.approval_status != APPROVAL_APPROVED:
        raise AppError("bad_request", "仅可启用或禁用已审批通过的用户", status_code=400)
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password", status_code=204)
def reset_password(
    user_id: int,
    body: AdminResetPasswordRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> None:
    user = _user_or_404(db, user_id)
    if user.is_superuser:
        raise AppError("bad_request", CANNOT_RESET_SUPERUSER_PASSWORD, status_code=400)
    user.password_hash = auth_service.hash_password(body.new_password)
    db.commit()

