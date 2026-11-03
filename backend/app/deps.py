from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.errors import AppError
from app.models.project.project import Project, ProjectMember
from app.models.identity.user import APPROVAL_APPROVED, APPROVAL_PENDING, APPROVAL_REJECTED, User
from app.services.ai.call_context import project_id_var, user_id_var
from app.services.identity import auth_service
from app.user_messages import (
    ACCOUNT_PENDING_APPROVAL,
    INSUFFICIENT_ROLE,
    NOT_AUTHENTICATED,
    NOT_PROJECT_MEMBER,
    PROJECT_NOT_FOUND,
    REGISTRATION_REJECTED,
    SUPERUSER_ONLY,
    TOKEN_INVALID,
    USER_DISABLED,
    USER_NOT_FOUND,
)

engine = create_engine(settings.mysql_dsn, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(engine, "connect")
def _set_mysql_beijing(dbapi_conn, connection_record) -> None:
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("SET time_zone = '+08:00'")
    finally:
        cursor.close()

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None:
        raise AppError("unauthorized", NOT_AUTHENTICATED, status_code=401)
    try:
        payload = auth_service.decode_token(creds.credentials)
    except ValueError:
        raise AppError("unauthorized", TOKEN_INVALID, status_code=401)
    if payload.get("typ") != "access":
        raise AppError("unauthorized", TOKEN_INVALID, status_code=401)
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError):
        raise AppError("unauthorized", TOKEN_INVALID, status_code=401)
    user = db.get(User, uid)
    if user is None:
        raise AppError("unauthorized", USER_NOT_FOUND, status_code=401)
    if user.approval_status == APPROVAL_PENDING:
        raise AppError("forbidden", ACCOUNT_PENDING_APPROVAL, status_code=403)
    if user.approval_status == APPROVAL_REJECTED:
        raise AppError("forbidden", REGISTRATION_REJECTED, status_code=403)
    if not user.is_active:
        raise AppError("inactive", USER_DISABLED, status_code=403)
    user_id_var.set(user.id)
    return user


ROLE_ORDER: dict[str, int] = {"viewer": 1, "editor": 2, "admin": 3}


def require_superuser(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_superuser:
        raise AppError("forbidden", SUPERUSER_ONLY, status_code=403)
    return user


def require_project_role(min_role: str) -> Callable[..., ProjectMember]:
    min_ord = ROLE_ORDER[min_role]

    def dependency(
        project_id: int,
        db: Annotated[Session, Depends(get_db)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> ProjectMember:
        if db.get(Project, project_id) is None:
            raise AppError("not_found", PROJECT_NOT_FOUND, status_code=404)
        row = (
            db.query(ProjectMember)
            .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
            .first()
        )
        if row is None:
            raise AppError("forbidden", NOT_PROJECT_MEMBER, status_code=403)
        if ROLE_ORDER[row.role] < min_ord:
            raise AppError("forbidden", INSUFFICIENT_ROLE, status_code=403)
        project_id_var.set(project_id)
        return row

    return dependency
