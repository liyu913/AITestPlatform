from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.functional.test_data_vars import (
    case_test_data_text,
    parse_test_data_text,
    resolve_case_variables,
    variables_to_text,
)
from app.services.identity import auth_service
from app.services.ui_automation.ui_run_executor import _build_ctx
from tests.conftest import TestingSessionLocal


def test_parse_test_data_text_kv_forms():
    assert parse_test_data_text("username：alice；password：secret") == {
        "username": "alice",
        "password": "secret",
    }
    assert parse_test_data_text("{{username}} = bob\npassword=x") == {
        "username": "bob",
        "password": "x",
    }
    assert parse_test_data_text("关键词 三室两厅") == {}


def test_case_test_data_text_prefers_variables():
    assert case_test_data_text(variables={"a": "1"}, test_data="b：2") == "a：1"
    assert case_test_data_text(variables={}, test_data=" b：2 ") == "b：2"
    assert variables_to_text({"password": "p"}) == "password：p"


def test_resolve_case_variables_merges_prose_structured_and_creds():
    out = resolve_case_variables(
        test_data="username：from_text；token：t1",
        variables={"username": "from_vars", "extra": "e"},
        credentials={"username": "from_creds", "password": "secret"},
    )
    assert out["username"] == "from_creds"
    assert out["password"] == "secret"
    assert out["extra"] == "e"
    assert out["token"] == "t1"
    prose_only = resolve_case_variables(test_data="username：alice；password：pw")
    assert prose_only == {"username": "alice", "password": "pw"}
    creds_only = resolve_case_variables(credentials={"user": "u", "passwd": "p"})
    assert creds_only["username"] == "u"
    assert creds_only["password"] == "p"


class _Env:
    variables_json = {"username": "env_user", "shared": "env"}
    secrets_encrypted = None
    accounts_json = [
        {"name": "admin", "variables": {"username": "acc_user", "shared": "acc"}},
    ]


def test_build_ctx_case_variables_override_env_and_account(monkeypatch):
    monkeypatch.setattr(
        "app.services.ui_automation.ui_run_executor.api_env_secrets.decode_secrets",
        lambda _enc: {"secret": "s", "shared": "sec"},
    )
    ctx = _build_ctx(
        _Env(),
        account_name="admin",
        case_variables={"shared": "case", "extra": "1"},
    )
    assert ctx["username"] == "acc_user"
    assert ctx["shared"] == "case"
    assert ctx["secret"] == "s"
    assert ctx["extra"] == "1"


def _user(username: str, pw: str) -> None:
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


def test_ui_case_variables_create_patch():
    _user("ui_vars", "v")
    tok = _login("ui_vars", "v")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    pr = client.post("/api/v1/projects/", headers=h, json={"name": "UV"})
    assert pr.status_code == 201
    pid = pr.json()["id"]
    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "with vars",
            "script": 'def run(page):\n    page.goto("/")\n',
            "variables": {"username": "u1", "password": "p1"},
            "status": "draft",
        },
    )
    assert cr.status_code == 201, cr.text
    assert cr.json()["variables"] == {"username": "u1", "password": "p1"}
    cid = cr.json()["id"]
    patched = client.patch(
        f"/api/v1/projects/{pid}/ui-cases/{cid}",
        headers=h,
        json={"variables": {"username": "u2"}},
    )
    assert patched.status_code == 200
    assert patched.json()["variables"] == {"username": "u2"}
