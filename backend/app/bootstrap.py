"""Ensure default superuser exists.

API 启动时调用 ``ensure_default_admin(reset_password=False)``：仅在账号不存在时创建。
CLI ``python -m app.bootstrap`` 强制重置密码（忘记密码时用）。

Defaults: admin / admin123（可用 BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_PASSWORD 覆盖）。
"""

from __future__ import annotations

import logging
import os
import sys

from sqlalchemy.orm import Session

from app.deps import SessionLocal
from app.models.identity.user import APPROVAL_APPROVED, User
from app.services.identity import auth_service

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"

log = logging.getLogger(__name__)


def _admin_credentials() -> tuple[str, str]:
    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", DEFAULT_PASSWORD) or DEFAULT_PASSWORD
    return username, password


def ensure_default_admin(*, reset_password: bool = False) -> str:
    """Create default superuser if missing.

    When ``reset_password`` is True (CLI), also reset password and re-assert
    superuser / approved / active for an existing account.
    Returns: ``created`` | ``updated`` | ``exists``.
    """
    username, password = _admin_credentials()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(
                username=username,
                password_hash=auth_service.hash_password(password),
                full_name="Admin",
                is_active=True,
                is_superuser=True,
                approval_status=APPROVAL_APPROVED,
            )
            db.add(user)
            db.commit()
            log.info("Created default superuser: %s", username)
            return "created"

        if not reset_password:
            return "exists"

        user.password_hash = auth_service.hash_password(password)
        user.is_active = True
        user.is_superuser = True
        user.approval_status = APPROVAL_APPROVED
        db.commit()
        log.info("Updated default superuser: %s", username)
        return "updated"
    finally:
        db.close()


def main() -> int:
    result = ensure_default_admin(reset_password=True)
    username, _ = _admin_credentials()
    if result == "created":
        print("Created superuser:", username)
    else:
        print("Updated superuser:", username)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
