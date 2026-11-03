from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _login(username: str, password: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_create_project_and_list_and_get():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="alice",
                password_hash=auth_service.hash_password("alicepw"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    token = _login("alice", "alicepw")
    client = TestClient(app)
    r = client.post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "P1", "description": "d"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["name"] == "P1"

    r2 = client.get("/api/v1/projects/", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["my_role"] == "admin"

    r3 = client.get(f"/api/v1/projects/{pid}", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 200
    assert r3.json()["id"] == pid


def test_admin_add_member_viewer_cannot_add():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="boss",
                password_hash=auth_service.hash_password("b"),
                is_active=True,
            )
        )
        db.add(
            User(
                username="viewer_user",
                password_hash=auth_service.hash_password("v"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    boss_tok = _login("boss", "b")
    client = TestClient(app)
    pr = client.post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "Team"},
    )
    pid = pr.json()["id"]

    r_add = client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"username": "viewer_user", "role": "viewer"},
    )
    assert r_add.status_code == 201
    assert r_add.json()["role"] == "viewer"

    view_tok = _login("viewer_user", "v")
    r_denied = client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {view_tok}"},
        json={"username": "viewer_user", "role": "editor"},
    )
    assert r_denied.status_code == 403

    r_list = client.get(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {view_tok}"},
    )
    assert r_list.status_code == 200
    members = r_list.json()
    assert len(members) == 2
    roles = {m["username"]: m["role"] for m in members}
    assert roles["boss"] == "admin"
    assert roles["viewer_user"] == "viewer"


def test_cannot_remove_last_admin():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="solo_user",
                password_hash=auth_service.hash_password("s"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    tok = _login("solo_user", "s")
    client = TestClient(app)
    pr = client.post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"name": "Solo"},
    )
    pid = pr.json()["id"]

    db = TestingSessionLocal()
    try:
        solo = db.query(User).filter(User.username == "solo_user").one()
        uid = solo.id
    finally:
        db.close()

    r = client.delete(
        f"/api/v1/projects/{pid}/members/{uid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400


def test_admin_can_delete_project():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="del_admin",
                password_hash=auth_service.hash_password("d"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    tok = _login("del_admin", "d")
    client = TestClient(app)

    pr = client.post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"name": "ToDelete"},
    )
    assert pr.status_code == 201
    pid = pr.json()["id"]

    r_del = client.delete(
        f"/api/v1/projects/{pid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_del.status_code == 204

    r_list = client.get("/api/v1/projects/", headers={"Authorization": f"Bearer {tok}"})
    assert r_list.status_code == 200
    assert r_list.json() == []

    r_get = client.get(f"/api/v1/projects/{pid}", headers={"Authorization": f"Bearer {tok}"})
    assert r_get.status_code == 404


def test_viewer_cannot_delete_project():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="del_boss",
                password_hash=auth_service.hash_password("b"),
                is_active=True,
            )
        )
        db.add(
            User(
                username="del_viewer",
                password_hash=auth_service.hash_password("v"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    boss_tok = _login("del_boss", "b")
    client = TestClient(app)
    pr = client.post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"name": "Protected"},
    )
    pid = pr.json()["id"]

    client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {boss_tok}"},
        json={"username": "del_viewer", "role": "viewer"},
    )

    view_tok = _login("del_viewer", "v")
    r_list = client.get("/api/v1/projects/", headers={"Authorization": f"Bearer {view_tok}"})
    assert r_list.status_code == 200
    assert r_list.json()[0]["my_role"] == "viewer"

    r_del = client.delete(
        f"/api/v1/projects/{pid}",
        headers={"Authorization": f"Bearer {view_tok}"},
    )
    assert r_del.status_code == 403
