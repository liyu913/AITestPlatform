"""Encrypt/decrypt api environment secrets dict."""

from __future__ import annotations

import json

from app.services.settings_crypto import decrypt_secret_optional, encrypt_secret


def encode_secrets(secrets: dict[str, str] | None) -> str | None:
    if not secrets:
        return None
    cleaned = {str(k): str(v) for k, v in secrets.items() if str(k).strip()}
    if not cleaned:
        return None
    return encrypt_secret(json.dumps(cleaned, ensure_ascii=False))


def decode_secrets(blob: str | None) -> dict[str, str]:
    plain = decrypt_secret_optional(blob)
    if not plain:
        return {}
    try:
        data = json.loads(plain)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def secret_keys(blob: str | None) -> list[str]:
    return sorted(decode_secrets(blob).keys())
