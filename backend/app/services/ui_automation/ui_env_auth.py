"""Encrypted Playwright storageState and browser inject for UI environments."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.services.api_automation import api_env_secrets
from app.services.settings_crypto import decrypt_secret_optional, encrypt_secret

AuthMode = Literal["none", "account", "token"]
TokenInject = Literal["none", "header", "local_storage"]

TOKEN_INJECT_NONE = "none"
TOKEN_INJECT_HEADER = "header"
TOKEN_INJECT_LOCAL_STORAGE = "local_storage"
TOKEN_INJECTS = (TOKEN_INJECT_NONE, TOKEN_INJECT_HEADER, TOKEN_INJECT_LOCAL_STORAGE)
AUTH_MODES = ("none", "account", "token")
_TOKEN_KEYS = ("token", "TOKEN", "access_token", "ACCESS_TOKEN")
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass
class BrowserAuth:
    mode: str = "none"
    account_name: str | None = None
    skip_login: bool = False
    storage_state: dict[str, Any] | None = None
    extra_http_headers: dict[str, str] | None = None
    local_storage: dict[str, str] | None = None
    token_present: bool = False
    storage_state_path: str | None = field(default=None, repr=False)

    def materialize_storage_path(self) -> str | None:
        if self.storage_state_path:
            return self.storage_state_path
        if not self.storage_state:
            return None
        self.storage_state_path = write_storage_state_temp(self.storage_state)
        return self.storage_state_path


def write_storage_state_temp(state: dict[str, Any]) -> str:
    fd, path = tempfile.mkstemp(prefix="raina-ui-auth-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    return path


def load_storage_state_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def decode_auth_states(blob: str | None) -> dict[str, dict[str, Any]]:
    plain = decrypt_secret_optional(blob)
    if not plain:
        return {}
    try:
        data = json.loads(plain)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, state in data.items():
        key = str(name or "").strip()
        if key and isinstance(state, dict):
            out[key] = state
    return out


def encode_auth_states(states: dict[str, dict[str, Any]] | None) -> str | None:
    if not states:
        return None
    cleaned = {
        str(k).strip(): v
        for k, v in states.items()
        if str(k).strip() and isinstance(v, dict)
    }
    if not cleaned:
        return None
    return encrypt_secret(json.dumps(cleaned, ensure_ascii=False))


def account_has_storage_state(env: Any, account_name: str | None) -> bool:
    name = (account_name or "").strip()
    if not name:
        return False
    return name in decode_auth_states(getattr(env, "auth_states_encrypted", None))


def account_auth_flags(env: Any, accounts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    states = decode_auth_states(getattr(env, "auth_states_encrypted", None))
    out: list[dict[str, Any]] = []
    for acc in accounts or []:
        if not isinstance(acc, dict) or not acc.get("name"):
            continue
        name = str(acc["name"])
        row = dict(acc)
        row["has_storage_state"] = name in states
        out.append(row)
    return out


def get_account_storage_state(env: Any, account_name: str | None) -> dict[str, Any] | None:
    name = (account_name or "").strip()
    if not name:
        return None
    return decode_auth_states(getattr(env, "auth_states_encrypted", None)).get(name)


def set_account_storage_state(env: Any, account_name: str, state: dict[str, Any]) -> None:
    name = (account_name or "").strip()
    if not name or not isinstance(state, dict):
        return
    states = decode_auth_states(getattr(env, "auth_states_encrypted", None))
    states[name] = state
    env.auth_states_encrypted = encode_auth_states(states)


def prune_account_storage_states(env: Any, keep_names: list[str] | None) -> None:
    keep = {str(n).strip() for n in (keep_names or []) if str(n).strip()}
    states = decode_auth_states(getattr(env, "auth_states_encrypted", None))
    pruned = {k: v for k, v in states.items() if k in keep}
    if pruned != states:
        env.auth_states_encrypted = encode_auth_states(pruned)


def _as_str_dict(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        key = str(k or "").strip()
        if not key:
            continue
        out[key] = "" if v is None else str(v)
    return out


def env_token_value(env: Any) -> str:
    secrets = api_env_secrets.decode_secrets(getattr(env, "secrets_encrypted", None))
    variables = getattr(env, "variables_json", None)
    vars_ = variables if isinstance(variables, dict) else {}
    for key in _TOKEN_KEYS:
        raw = secrets.get(key) or vars_.get(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    return ""


def token_from_vars(vars_: dict[str, str] | None) -> str:
    if not isinstance(vars_, dict):
        return ""
    for key in _TOKEN_KEYS:
        raw = vars_.get(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    return ""


def resolve_token_value(
    env: Any,
    *,
    override: str | None = None,
    extra_vars: dict[str, str] | None = None,
) -> str:
    """Prefer run/case override, then case/run variables, then env secrets/vars."""
    raw = (override or "").strip()
    if raw:
        return raw
    from_vars = token_from_vars(extra_vars)
    if from_vars:
        return from_vars
    return env_token_value(env)


def normalize_auth_mode(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if raw in AUTH_MODES:
        return raw
    return None


def effective_run_auth_mode(
    run_auth_mode: str | None,
    case_auth_mode: str | None = None,
) -> str | None:
    """Resolve auth for a case/suite item.

    Returns ``none`` / ``account`` / ``token`` when explicitly chosen, or ``None``
    to follow the environment (``apply_env_defaults``).

    Case ``none`` is a durable opt-out (login cases skip env inject).
    """
    run = normalize_auth_mode(run_auth_mode)
    if run is not None:
        return run
    case = normalize_auth_mode(case_auth_mode)
    if case in ("none", "account", "token"):
        return case
    return None


def normalize_token_inject(value: str | None) -> str:
    raw = (value or TOKEN_INJECT_NONE).strip().lower()
    if raw in TOKEN_INJECTS:
        return raw
    return TOKEN_INJECT_NONE


def raw_inject_templates(env: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Return (headers_tpl, local_storage_tpl). Prefer KV fields; else legacy token_inject."""
    headers = _as_str_dict(getattr(env, "inject_headers_json", None))
    local_storage = _as_str_dict(getattr(env, "inject_local_storage_json", None))
    if headers or local_storage:
        return headers, local_storage
    mode = normalize_token_inject(getattr(env, "token_inject", None))
    if mode == TOKEN_INJECT_HEADER:
        return {"Authorization": "Bearer {{token}}"}, {}
    if mode == TOKEN_INJECT_LOCAL_STORAGE:
        key = str(getattr(env, "token_storage_key", None) or "token").strip() or "token"
        return {}, {key: "{{token}}"}
    return {}, {}


def env_has_inject_config(env: Any) -> bool:
    headers, local_storage = raw_inject_templates(env)
    return bool(headers or local_storage)


def build_inject_context(
    env: Any,
    *,
    extra_vars: dict[str, str] | None = None,
    token_override: str | None = None,
) -> dict[str, str]:
    ctx: dict[str, str] = {}
    variables = getattr(env, "variables_json", None)
    if isinstance(variables, dict):
        ctx.update({str(k): str(v) for k, v in variables.items() if str(k).strip()})
    secrets = api_env_secrets.decode_secrets(getattr(env, "secrets_encrypted", None))
    ctx.update({str(k): str(v) for k, v in secrets.items() if str(k).strip()})
    if isinstance(extra_vars, dict):
        ctx.update({str(k): str(v) for k, v in extra_vars.items() if str(k).strip()})
    override = (token_override or "").strip()
    if override:
        ctx["token"] = override
        ctx["access_token"] = override
    token = resolve_token_value(env, override=token_override, extra_vars=extra_vars)
    if token:
        for key in ("token", "access_token", "TOKEN", "ACCESS_TOKEN"):
            ctx.setdefault(key, token)
    return ctx


def render_inject_map(raw: dict[str, str], ctx: dict[str, str]) -> dict[str, str]:
    """Render {{var}} templates; skip entries with missing/empty vars."""
    out: dict[str, str] = {}
    for key, value in (raw or {}).items():
        text = "" if value is None else str(value)
        needed = _VAR_RE.findall(text)
        if any(not str(ctx.get(name) or "").strip() for name in needed):
            continue

        def repl(m: re.Match[str]) -> str:
            return str(ctx.get(m.group(1)) or "")

        rendered = _VAR_RE.sub(repl, text)
        out[str(key)] = rendered
    return out


def resolve_inject_payload(
    env: Any,
    *,
    extra_vars: dict[str, str] | None = None,
    token_override: str | None = None,
) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    headers_tpl, local_tpl = raw_inject_templates(env)
    if not headers_tpl and not local_tpl:
        return None, None
    ctx = build_inject_context(env, extra_vars=extra_vars, token_override=token_override)
    headers = render_inject_map(headers_tpl, ctx)
    local_storage = render_inject_map(local_tpl, ctx)
    return (headers or None), (local_storage or None)


def _token_inject_payload(env: Any, token: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Legacy helper kept for tests; prefer resolve_inject_payload."""
    if not token:
        return None, None
    mode = normalize_token_inject(getattr(env, "token_inject", None))
    if mode == TOKEN_INJECT_LOCAL_STORAGE:
        key = str(getattr(env, "token_storage_key", None) or "token").strip() or "token"
        return None, {key: token}
    if mode == TOKEN_INJECT_HEADER:
        return {"Authorization": f"Bearer {token}"}, None
    return None, None


def resolve_browser_auth(
    env: Any,
    *,
    auth_mode: str = "none",
    account_name: str | None = None,
    apply_env_defaults: bool = False,
    token_override: str | None = None,
    token_vars: dict[str, str] | None = None,
) -> BrowserAuth:
    """Build inject options for explore / dry-run / saved-case run.

    Inject KV (or legacy token_inject) applies when auth_mode is token, or when
    apply_env_defaults and inject config exists. Explicit auth_mode=none opts out.
    """
    mode = (auth_mode or "none").strip().lower()
    if mode not in AUTH_MODES:
        mode = "none"
    name = (account_name or "").strip() or None
    token = resolve_token_value(env, override=token_override, extra_vars=token_vars)
    has_inject = env_has_inject_config(env)

    if mode == "account" or (apply_env_defaults and name):
        state = get_account_storage_state(env, name) if name else None
        if state:
            return BrowserAuth(
                mode="account",
                account_name=name,
                skip_login=True,
                storage_state=state,
                token_present=bool(token),
            )
        if mode == "account":
            return BrowserAuth(
                mode="account",
                account_name=name,
                skip_login=False,
                token_present=bool(token),
            )

    if mode == "none" and not apply_env_defaults:
        return BrowserAuth(mode="none", token_present=bool(token))

    should_inject = mode == "token" or (apply_env_defaults and has_inject)
    if not should_inject:
        return BrowserAuth(mode="none", token_present=bool(token))

    headers, local_storage = resolve_inject_payload(
        env, extra_vars=token_vars, token_override=token_override
    )
    if mode == "token" and not headers and not local_storage and token and not has_inject:
        headers = {"Authorization": f"Bearer {token}"}
    present = bool(headers or local_storage or token)
    return BrowserAuth(
        mode="token",
        skip_login=bool(headers or local_storage),
        extra_http_headers=headers,
        local_storage=local_storage,
        token_present=present,
    )


def local_storage_init_script(pairs: dict[str, str] | None) -> str | None:
    if not pairs:
        return None
    payload = {str(k): str(v) for k, v in pairs.items() if str(k)}
    if not payload:
        return None
    blob = json.dumps(payload, ensure_ascii=False)
    return (
        "(() => { const data = "
        + blob
        + "; for (const [k, v] of Object.entries(data)) {"
        + " try { localStorage.setItem(k, String(v)); } catch (e) {} } })();"
    )
