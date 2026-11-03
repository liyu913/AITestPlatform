from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


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


def _project(token: str, name: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_module_tree_crud_and_api_definition_link():
    _user("mod_crud", "p")
    tok = _login("mod_crud", "p")
    pid = _project(tok, "ModCrud")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    root = client.post(
        f"/api/v1/projects/{pid}/modules/",
        headers=h,
        json={"name": "用户", "description": "user domain"},
    )
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]

    child = client.post(
        f"/api/v1/projects/{pid}/modules/",
        headers=h,
        json={"name": "登录", "parent_id": root_id},
    )
    assert child.status_code == 201, child.text
    child_id = child.json()["id"]

    dup = client.post(
        f"/api/v1/projects/{pid}/modules/",
        headers=h,
        json={"name": "登录", "parent_id": root_id},
    )
    assert dup.status_code == 409

    gchild = client.post(
        f"/api/v1/projects/{pid}/modules/",
        headers=h,
        json={"name": "短信", "parent_id": child_id},
    )
    assert gchild.status_code == 201, gchild.text
    gchild_id = gchild.json()["id"]

    too_deep = client.post(
        f"/api/v1/projects/{pid}/modules/",
        headers=h,
        json={"name": "超深", "parent_id": gchild_id},
    )
    assert too_deep.status_code == 400

    tree = client.get(f"/api/v1/projects/{pid}/modules/tree", headers=h)
    assert tree.status_code == 200
    assert tree.json()[0]["name"] == "用户"
    assert tree.json()[0]["children"][0]["name"] == "登录"

    no_del = client.delete(f"/api/v1/projects/{pid}/modules/{root_id}", headers=h)
    assert no_del.status_code == 409

    assert client.delete(f"/api/v1/projects/{pid}/modules/{gchild_id}", headers=h).status_code == 204
    assert client.delete(f"/api/v1/projects/{pid}/modules/{child_id}", headers=h).status_code == 204

    created = client.post(
        f"/api/v1/projects/{pid}/api-definitions/",
        headers=h,
        json={
            "name": "Get User",
            "method": "GET",
            "path": "/users/me",
            "module_id": root_id,
            "body_type": "none",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["module_id"] == root_id
    assert created.json()["module"] == "用户"

    listed = client.get(
        f"/api/v1/projects/{pid}/api-definitions/?module_id={root_id}",
        headers=h,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    cleared = client.patch(
        f"/api/v1/projects/{pid}/api-definitions/{created.json()['id']}",
        headers=h,
        json={"module_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["module_id"] is None
    assert cleared.json()["module"] == ""

    force = client.delete(f"/api/v1/projects/{pid}/modules/{root_id}?force=true", headers=h)
    assert force.status_code == 204
