"""UI case auth_mode defaults and run-time token/variable overrides."""

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
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


def _project(token: str, name: str = "UICASEAUTH") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_case_auth_mode_and_run_options(monkeypatch):
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("ui_case_auth", "p")
    tok = _login("ui_case_auth", "p")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "stg",
            "base_url": "https://app.example.com",
            "is_default": True,
            "secrets": {"token": "env-secret"},
            "variables": {"base": "from-env"},
            "token_inject": "header",
            "accounts": [{"name": "admin", "variables": {"user": "a"}}],
        },
    )
    assert er.status_code == 201, er.text

    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "token case",
            "script": "def run(page):\n    page.goto('/')\n",
            "status": "ready",
            "auth_mode": "token",
            "variables": {"role": "qa"},
        },
    )
    assert cr.status_code == 201, cr.text
    case = cr.json()
    assert case["auth_mode"] == "token"
    assert case["variables"]["role"] == "qa"
    case_id = case["id"]

    # Follow-env case: clear auth by creating without auth_mode
    cr2 = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "plain",
            "script": "def run(page):\n    page.goto('/')\n",
            "status": "ready",
        },
    )
    assert cr2.status_code == 201
    assert cr2.json().get("auth_mode") is None

    # Run with override variables; do not execute (async enqueue may noop in tests)
    rr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/{case_id}/run",
        headers=h,
        json={
            "auth_mode": "token",
            "variables": {"token": "run-override", "extra": "1"},
            "headless": True,
        },
    )
    assert rr.status_code == 201, rr.text
    run = rr.json()
    opts = (run.get("summary_json") or {}).get("run_options") or {}
    assert opts.get("auth_mode") == "token"
    assert opts.get("variables", {}).get("token") == "run-override"
    assert opts.get("variables", {}).get("extra") == "1"

    # Case default auth_mode used when request omits it
    rr2 = client.post(
        f"/api/v1/projects/{pid}/ui-cases/{case_id}/run",
        headers=h,
        json={},
    )
    assert rr2.status_code == 201, rr2.text
    opts2 = (rr2.json().get("summary_json") or {}).get("run_options") or {}
    assert opts2.get("auth_mode") == "token"

    # Case auth_mode=none + omit request → durable opt-out in run_options
    none_case = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "none-default",
            "script": "def run(page):\n    page.goto('/')\n",
            "status": "ready",
            "auth_mode": "none",
        },
    )
    assert none_case.status_code == 201, none_case.text
    assert none_case.json()["auth_mode"] == "none"
    none_id = none_case.json()["id"]
    rr_opt_out = client.post(
        f"/api/v1/projects/{pid}/ui-cases/{none_id}/run",
        headers=h,
        json={},
    )
    assert rr_opt_out.status_code == 201, rr_opt_out.text
    opts_opt_out = (rr_opt_out.json().get("summary_json") or {}).get("run_options") or {}
    assert opts_opt_out.get("auth_mode") == "none"

    # Explicit none on request → also stored as opt-out
    rr_none = client.post(
        f"/api/v1/projects/{pid}/ui-cases/{none_id}/run",
        headers=h,
        json={"auth_mode": "none"},
    )
    assert rr_none.status_code == 201, rr_none.text
    opts_none = (rr_none.json().get("summary_json") or {}).get("run_options") or {}
    assert opts_none.get("auth_mode") == "none"

    # Variables-only token env + follow-env case (plain) omits auth_mode
    plain_id = cr2.json()["id"]
    rr_plain = client.post(
        f"/api/v1/projects/{pid}/ui-cases/{plain_id}/run",
        headers=h,
        json={},
    )
    assert rr_plain.status_code == 201, rr_plain.text
    opts_plain = (rr_plain.json().get("summary_json") or {}).get("run_options") or {}
    assert opts_plain.get("auth_mode") is None

    patched = client.patch(
        f"/api/v1/projects/{pid}/ui-cases/{case_id}",
        headers=h,
        json={"auth_mode": "account", "account_name": "admin"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["auth_mode"] == "account"
    assert patched.json()["account_name"] == "admin"

    cleared = client.patch(
        f"/api/v1/projects/{pid}/ui-cases/{case_id}",
        headers=h,
        json={"auth_mode": None, "account_name": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json().get("auth_mode") is None
    assert cleared.json().get("account_name") is None
