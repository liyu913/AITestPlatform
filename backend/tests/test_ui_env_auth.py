from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.ui_automation.playwright_cli_session import PlaywrightCliSession
from app.services.ui_automation.ui_env_auth import (
    account_auth_flags,
    decode_auth_states,
    effective_run_auth_mode,
    encode_auth_states,
    local_storage_init_script,
    resolve_browser_auth,
    set_account_storage_state,
)
from tests.conftest import TestingSessionLocal


def _user(username: str, pw: str):
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str, name: str = "UIAUTH") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_encode_decode_auth_states(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    blob = encode_auth_states({"admin": {"cookies": [{"name": "sid", "value": "abc"}]}})
    assert blob
    decoded = decode_auth_states(blob)
    assert decoded["admin"]["cookies"][0]["value"] == "abc"


def test_account_auth_flags_hide_cookie_values(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    env = SimpleNamespace(auth_states_encrypted=None)
    set_account_storage_state(env, "admin", {"cookies": [{"name": "sid", "value": "secret"}]})
    flags = account_auth_flags(
        env,
        [{"name": "admin", "variables": {"user": "a"}}, {"name": "guest", "variables": {}}],
    )
    assert flags[0]["has_storage_state"] is True
    assert flags[1]["has_storage_state"] is False
    blob = str(env.auth_states_encrypted or "")
    assert "secret" not in blob


def test_effective_run_auth_mode_follow_env():
    assert effective_run_auth_mode(None, None) is None
    # Case none is durable opt-out (login / skip inject)
    assert effective_run_auth_mode(None, "none") == "none"
    assert effective_run_auth_mode(None, "token") == "token"
    assert effective_run_auth_mode(None, "account") == "account"
    assert effective_run_auth_mode("none", "token") == "none"
    assert effective_run_auth_mode("token", "none") == "token"


def test_resolve_browser_auth_token_header(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    from app.services.api_automation import api_env_secrets

    env = SimpleNamespace(
        auth_states_encrypted=None,
        secrets_encrypted=api_env_secrets.encode_secrets({"token": "tok-1"}),
        variables_json={},
        token_inject="header",
        token_storage_key=None,
        inject_headers_json=None,
        inject_local_storage_json=None,
    )
    auth = resolve_browser_auth(env, auth_mode="token")
    assert auth.skip_login is True
    assert auth.extra_http_headers["Authorization"] == "Bearer tok-1"
    assert auth.local_storage is None


def test_resolve_browser_auth_inject_kv_template(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    env = SimpleNamespace(
        auth_states_encrypted=None,
        secrets_encrypted=None,
        variables_json={"access_token": "jwt-from-vars"},
        token_inject="none",
        token_storage_key=None,
        inject_headers_json={},
        inject_local_storage_json={"access_token": "{{access_token}}"},
    )
    auth = resolve_browser_auth(env, apply_env_defaults=True)
    assert auth.mode == "token"
    assert auth.local_storage == {"access_token": "jwt-from-vars"}
    assert auth.extra_http_headers is None


def test_resolve_browser_auth_apply_env_defaults_token_inject(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    from app.services.api_automation import api_env_secrets

    env = SimpleNamespace(
        auth_states_encrypted=None,
        secrets_encrypted=None,
        variables_json={"token": "from-vars"},
        token_inject="header",
        token_storage_key=None,
        inject_headers_json=None,
        inject_local_storage_json=None,
    )
    # Follow-env: no auth_mode, apply_env_defaults → inject
    auth = resolve_browser_auth(env, apply_env_defaults=True)
    assert auth.mode == "token"
    assert auth.extra_http_headers["Authorization"] == "Bearer from-vars"
    # Explicit none opts out even when env has token_inject
    none_auth = resolve_browser_auth(env, auth_mode="none", apply_env_defaults=False)
    assert none_auth.mode == "none"
    assert none_auth.extra_http_headers is None
    # Variables-only token + local_storage inject
    env.token_inject = "local_storage"
    env.token_storage_key = "access_token"
    ls = resolve_browser_auth(env, apply_env_defaults=True)
    assert ls.local_storage == {"access_token": "from-vars"}
    assert ls.extra_http_headers is None


def test_resolve_browser_auth_token_vars_override_env(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    from app.services.api_automation import api_env_secrets

    env = SimpleNamespace(
        auth_states_encrypted=None,
        secrets_encrypted=api_env_secrets.encode_secrets({"token": "env-tok"}),
        variables_json={},
        token_inject="header",
        token_storage_key=None,
        inject_headers_json=None,
        inject_local_storage_json=None,
    )
    auth = resolve_browser_auth(env, auth_mode="token", token_vars={"token": "run-tok"})
    assert auth.extra_http_headers["Authorization"] == "Bearer run-tok"
    override = resolve_browser_auth(
        env, auth_mode="token", token_override="explicit", token_vars={"token": "run-tok"}
    )
    assert override.extra_http_headers["Authorization"] == "Bearer explicit"


def test_resolve_inject_kv_case_vars_override_and_none_skips(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    env = SimpleNamespace(
        auth_states_encrypted=None,
        secrets_encrypted=None,
        variables_json={"foo": "env", "access_token": "env-tok"},
        token_inject="none",
        token_storage_key=None,
        inject_headers_json={},
        inject_local_storage_json={"access_token": "{{access_token}}", "foo": "{{foo}}"},
    )
    follow = resolve_browser_auth(env, apply_env_defaults=True)
    assert follow.local_storage == {"access_token": "env-tok", "foo": "env"}

    overridden = resolve_browser_auth(
        env, apply_env_defaults=True, token_vars={"foo": "case", "access_token": "case-tok"}
    )
    assert overridden.local_storage == {"access_token": "case-tok", "foo": "case"}

    skipped = resolve_browser_auth(env, auth_mode="none")
    assert skipped.mode == "none"
    assert skipped.local_storage is None
    assert skipped.extra_http_headers is None


def test_resolve_browser_auth_account_storage(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    env = SimpleNamespace(
        auth_states_encrypted=None,
        secrets_encrypted=None,
        variables_json={},
        token_inject="none",
        inject_headers_json=None,
        inject_local_storage_json=None,
    )
    set_account_storage_state(env, "admin", {"cookies": []})
    auth = resolve_browser_auth(env, auth_mode="account", account_name="admin")
    assert auth.skip_login is True
    assert auth.storage_state == {"cookies": []}
    missing = resolve_browser_auth(env, auth_mode="account", account_name="guest")
    assert missing.skip_login is False


def test_cli_context_options_include_storage_and_headers():
    with patch("app.services.ui_automation.playwright_cli_session.cli_ready", return_value=None):
        cli = PlaywrightCliSession(
            storage_state={"cookies": []},
            extra_http_headers={"Authorization": "Bearer x"},
            local_storage={"token": "x"},
        )
    opts = cli._context_options()
    assert opts["storage_state"] == {"cookies": []}
    assert opts["extra_http_headers"]["Authorization"] == "Bearer x"
    script = local_storage_init_script({"token": "abc"})
    assert "localStorage.setItem" in script
    assert "abc" in script


def test_env_api_token_inject_and_no_auth_leak(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("ui_auth", "p")
    tok = _login("ui_auth", "p")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "stg",
            "base_url": "https://app.example.com",
            "accounts": [{"name": "admin", "variables": {"user": "a"}}],
            "secrets": {"token": "super-secret"},
            "token_inject": "header",
        },
    )
    assert er.status_code == 201, er.text
    env = er.json()
    assert env["token_inject"] == "header"
    assert env["accounts"][0]["has_storage_state"] is False
    assert "auth_states" not in env
    assert "super-secret" not in str(env)
    assert "token" in env["secret_keys"]

    pr = client.patch(
        f"/api/v1/projects/{pid}/ui-envs/{env['id']}",
        headers=h,
        json={
            "inject_local_storage": {"access_token": "{{access_token}}"},
            "inject_headers": {},
            "token_inject": "none",
        },
    )
    assert pr.status_code == 200, pr.text
    body = pr.json()
    assert body["inject_local_storage"] == {"access_token": "{{access_token}}"}
    assert body["token_inject"] == "none"
