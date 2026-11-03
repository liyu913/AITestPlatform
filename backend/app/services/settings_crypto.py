"""Encrypt/decrypt secrets stored in AI profiles / env settings using Fernet."""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

log = logging.getLogger(__name__)


def _fernet() -> Fernet:
    key = (settings.settings_encryption_key or "").strip()
    if not key:
        raise ValueError("SETTINGS_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode("ascii"))


def encrypt_secret(plain: str) -> str:
    if not plain:
        raise ValueError("secret must be non-empty")
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret_optional(blob: str | None) -> str | None:
    """Returns decrypted text, or None if missing / decrypt failure."""
    if not blob or not str(blob).strip():
        return None
    try:
        raw = _fernet().decrypt(str(blob).strip().encode("ascii"))
        return raw.decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as e:
        log.warning("settings decrypt failed, falling back to env: %s", e)
        return None
