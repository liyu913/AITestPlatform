from fastapi.testclient import TestClient

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


def _project(token: str, name: str = "AI-Session-P") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _add_member(pid: int, username: str, role: str = "editor"):
    db = TestingSessionLocal()
    try:
        from app.models.project.project import ProjectMember

        u = db.query(User).filter(User.username == username).one()
        db.add(ProjectMember(project_id=pid, user_id=u.id, role=role))
        db.commit()
    finally:
        db.close()


def test_session_crud():
    _user("sess_a", "a")
    tok = _login("sess_a", "a")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    r_create = client.post(f"/api/v1/projects/{pid}/ai/sessions", headers=headers)
    assert r_create.status_code == 201
    session = r_create.json()
    sid = session["id"]
    assert session["title"] == "新对话"
    assert session["messages"] == []

    r_list = client.get(f"/api/v1/projects/{pid}/ai/sessions", headers=headers)
    assert r_list.status_code == 200
    assert len(r_list.json()) == 1
    assert r_list.json()[0]["message_count"] == 0

    r_update = client.put(
        f"/api/v1/projects/{pid}/ai/sessions/{sid}",
        headers=headers,
        json={
            "title": "登录用例讨论",
            "messages": [{"role": "user", "content": "生成登录用例"}],
            "drafts": [
                {
                    "title": "草稿1",
                    "steps": [{"order": 0, "action": "a", "expected": "e"}],
                }
            ],
            "llm_model": "gpt-4o",
        },
    )
    assert r_update.status_code == 200
    body = r_update.json()
    assert body["title"] == "登录用例讨论"
    assert len(body["messages"]) == 1
    assert len(body["drafts"]) == 1
    assert body["llm_model"] == "gpt-4o"

    r_get = client.get(f"/api/v1/projects/{pid}/ai/sessions/{sid}", headers=headers)
    assert r_get.status_code == 200
    assert r_get.json()["title"] == "登录用例讨论"

    r_delete = client.delete(f"/api/v1/projects/{pid}/ai/sessions/{sid}", headers=headers)
    assert r_delete.status_code == 204

    r_list2 = client.get(f"/api/v1/projects/{pid}/ai/sessions", headers=headers)
    assert r_list2.status_code == 200
    assert r_list2.json() == []


def test_session_auto_title_from_first_user_message():
    _user("sess_title", "a")
    tok = _login("sess_title", "a")
    pid = _project(tok, "AI-Title")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    r_create = client.post(f"/api/v1/projects/{pid}/ai/sessions", headers=headers)
    sid = r_create.json()["id"]

    r_update = client.put(
        f"/api/v1/projects/{pid}/ai/sessions/{sid}",
        headers=headers,
        json={
            "messages": [{"role": "user", "content": "生成登录与注册相关用例覆盖异常路径"}],
            "drafts": [],
        },
    )
    assert r_update.status_code == 200
    assert r_update.json()["title"].startswith("生成登录与注册相关用例")


def test_session_isolated_between_users():
    _user("sess_owner", "a")
    _user("sess_other", "b")
    tok_owner = _login("sess_owner", "a")
    tok_other = _login("sess_other", "b")
    pid = _project(tok_owner)
    _add_member(pid, "sess_other", "editor")

    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/ai/sessions",
        headers={"Authorization": f"Bearer {tok_owner}"},
    )
    sid = r.json()["id"]

    r_other_list = client.get(
        f"/api/v1/projects/{pid}/ai/sessions",
        headers={"Authorization": f"Bearer {tok_other}"},
    )
    assert r_other_list.status_code == 200
    assert r_other_list.json() == []

    r_other_get = client.get(
        f"/api/v1/projects/{pid}/ai/sessions/{sid}",
        headers={"Authorization": f"Bearer {tok_other}"},
    )
    assert r_other_get.status_code == 404
