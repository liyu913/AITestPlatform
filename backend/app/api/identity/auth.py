from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_db
from app.errors import AppError
from app.models.identity.user import APPROVAL_APPROVED, APPROVAL_PENDING, APPROVAL_REJECTED, User
from app.schemas.identity.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterOptionsOut,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from app.services.identity import auth_service
from app.services.identity import captcha_code
from app.user_messages import (
    ACCOUNT_PENDING_APPROVAL,
    CAPTCHA_INVALID,
    USERNAME_ALREADY_REGISTERED,
    LOGIN_INVALID_CREDENTIALS,
    REFRESH_TOKEN_INVALID,
    REGISTER_PENDING_MESSAGE,
    REGISTRATION_DISABLED,
    REGISTRATION_REJECTED,
    TOKEN_INVALID,
    USER_DISABLED,
    USER_NOT_FOUND_OR_INACTIVE,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _verify_captcha_if_required(captcha_id: str | None, code: str | None) -> None:
    if not settings.require_login_captcha:
        return
    if not captcha_code.verify_and_consume(captcha_id, code):
        raise AppError("bad_request", CAPTCHA_INVALID, status_code=400)


def _tokens_for_user(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=auth_service.create_access_token(user.id),
        refresh_token=auth_service.create_refresh_token(user.id),
    )


@router.get("/captcha")
def get_captcha():
    cid, b64 = captcha_code.create_captcha()
    return {"captcha_id": cid, "image_base64": b64}


@router.get("/register-options", response_model=RegisterOptionsOut)
def register_options() -> RegisterOptionsOut:
    return RegisterOptionsOut(
        allowed=settings.allow_open_registration,
        require_captcha=settings.require_login_captcha,
    )


def _ensure_user_can_login(user: User) -> None:
    if user.approval_status == APPROVAL_PENDING:
        raise AppError("forbidden", ACCOUNT_PENDING_APPROVAL, status_code=403)
    if user.approval_status == APPROVAL_REJECTED:
        raise AppError("forbidden", REGISTRATION_REJECTED, status_code=403)
    if not user.is_active:
        raise AppError("inactive", USER_DISABLED, status_code=403)


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    if not settings.allow_open_registration:
        raise AppError("forbidden", REGISTRATION_DISABLED, status_code=403)
    _verify_captcha_if_required(body.captcha_id, body.captcha_code)
    if db.query(User).filter(User.username == body.username).first():
        raise AppError("conflict", USERNAME_ALREADY_REGISTERED, status_code=409)
    user = User(
        username=body.username,
        password_hash=auth_service.hash_password(body.password),
        full_name=body.full_name,
        is_active=False,
        is_superuser=False,
        approval_status=APPROVAL_PENDING,
    )
    db.add(user)
    db.commit()
    return RegisterResponse(message=REGISTER_PENDING_MESSAGE)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    _verify_captcha_if_required(body.captcha_id, body.captcha_code)
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not auth_service.verify_password(body.password, user.password_hash):
        raise AppError("unauthorized", LOGIN_INVALID_CREDENTIALS, status_code=401)
    _ensure_user_can_login(user)
    return _tokens_for_user(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        payload = auth_service.decode_token(body.refresh_token)
    except ValueError:
        raise AppError("unauthorized", REFRESH_TOKEN_INVALID, status_code=401)
    if payload.get("typ") != "refresh":
        raise AppError("unauthorized", TOKEN_INVALID, status_code=401)
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise AppError("unauthorized", REFRESH_TOKEN_INVALID, status_code=401)
    user = db.get(User, user_id)
    if user is None:
        raise AppError("unauthorized", USER_NOT_FOUND_OR_INACTIVE, status_code=401)
    try:
        _ensure_user_can_login(user)
    except AppError:
        raise AppError("unauthorized", USER_NOT_FOUND_OR_INACTIVE, status_code=401) from None
    return TokenResponse(
        access_token=auth_service.create_access_token(user.id),
        refresh_token=auth_service.create_refresh_token(user.id),
    )
