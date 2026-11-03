from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.errors import AppError
from app.user_messages import CURRENT_PASSWORD_WRONG, SUPERUSER_ONLY, USERNAME_ALREADY_REGISTERED
from app.models.identity.user import APPROVAL_APPROVED, User
from app.schemas.identity.user import ChangePasswordRequest, UserCreateRequest, UserOut
from app.services.identity import auth_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def read_me(current: User = Depends(get_current_user)) -> User:
    return current


@router.post("/me/password", status_code=204)
def change_my_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    if not auth_service.verify_password(body.current_password, current.password_hash):
        raise AppError("bad_request", CURRENT_PASSWORD_WRONG, status_code=400)
    current.password_hash = auth_service.hash_password(body.new_password)
    db.commit()


@router.post("/", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> User:
    if not current.is_superuser:
        raise AppError("forbidden", SUPERUSER_ONLY, status_code=403)
    if db.query(User).filter(User.username == body.username).first():
        raise AppError("conflict", USERNAME_ALREADY_REGISTERED, status_code=409)
    u = User(
        username=body.username,
        password_hash=auth_service.hash_password(body.password),
        full_name=body.full_name,
        is_active=True,
        is_superuser=False,
        approval_status=APPROVAL_APPROVED,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u
