"""Parse / normalize case-level test data variables."""

from __future__ import annotations

import re

_KV_SPLIT = re.compile(r"[;；\n|/]+")
_PAIR = re.compile(
    r"^\s*"
    r"(?:\{\{\s*)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\}\})?"
    r"\s*[：:=\-]\s*"
    r"(.+?)\s*$"
)


def as_str_dict(data: object | None) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = "" if v is None else str(v)
    return out


def parse_test_data_text(text: str | None) -> dict[str, str]:
    """Best-effort parse free-form test_data into key/value pairs."""
    raw = (text or "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for part in _KV_SPLIT.split(raw):
        chunk = part.strip()
        if not chunk:
            continue
        m = _PAIR.match(chunk)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key and value:
            out[key] = value
    return out


def variables_to_text(variables: dict[str, str] | None) -> str:
    """Serialize variables to Chinese key-value prose for explore / AI consumers."""
    items = as_str_dict(variables)
    if not items:
        return ""
    return "；".join(f"{k}：{v}" for k, v in items.items())


def case_test_data_text(*, variables: object | None = None, test_data: str | None = None) -> str:
    """Prefer structured variables; fall back to free-form test_data."""
    text = variables_to_text(as_str_dict(variables))
    if text:
        return text
    return (test_data or "").strip()


def resolve_case_variables(
    *,
    variables: object | None = None,
    test_data: str | None = None,
    credentials: object | None = None,
) -> dict[str, str]:
    """Merge test_data prose, structured variables, then credentials into one dict.

    Precedence (later wins): parse(test_data) < variables < credentials username/password.
    """
    out = parse_test_data_text(test_data)
    out.update(as_str_dict(variables))
    creds = as_str_dict(credentials)
    if not creds:
        return out
    user = str(creds.get("username") or creds.get("user") or "").strip()
    pwd = str(creds.get("password") or creds.get("passwd") or "").strip()
    if user:
        out["username"] = user
        out.setdefault("user", user)
    if pwd:
        out["password"] = pwd
        out.setdefault("passwd", pwd)
    for k, v in creds.items():
        if k in ("username", "user", "password", "passwd"):
            continue
        if v:
            out[k] = v
    return out
