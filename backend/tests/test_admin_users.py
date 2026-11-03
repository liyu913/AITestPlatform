from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import APPROVAL_APPROVED, APPROVAL_PENDING, APPROVAL_REJECTED, User
from app.services.identity import auth_service
from app.user_messages import (
    ACCOUNT_PENDING_APPROVAL,
    CANNOT_RESET_SUPERUSER_PASSWORD,
    REGISTRATION_REJECTED,
    USER_DISABLED,
)
from tests.conftest import TestingSessionLocal


def _su(username: str, pw: str) -> str:
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password(pw),
                is_active=True,
                is_superuser=True,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.commit()
    finally:
        db.close()
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _pending_user(username: str, pw: str) -> User:
    db = TestingSessionLocal()
    try:
        user = User(
            username=username,
            password_hash=auth_service.hash_password(pw),
            is_active=False,
            is_superuser=False,
            approval_status=APPROVAL_PENDING,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def test_list_users_forbidden_for_non_superuser():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="nonadmin",
                password_hash=auth_service.hash_password("p"),
                is_active=True,
                is_superuser=False,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"username": "nonadmin", "password": "p"})
    token = r.json()["access_token"]
    r2 = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 403


def test_list_pending_users():
    _pending_user("pend1", "pw123456")
    _pending_user("pend2", "pw123456")
    token = _su("admin_user", "adminpw")

    client = TestClient(app)
    r = client.get(
        "/api/v1/admin/users?status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    usernames = {u["username"] for u in data["items"]}
    assert "pend1" in usernames
    assert "pend2" in usernames
    for u in data["items"]:
        if u["username"] in ("pend1", "pend2"):
            assert u["approval_status"] == "pending"


def test_approve_user():
    _pending_user("approve_me", "pw123456")
    token = _su("admin2", "adminpw")

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.username == "approve_me").one()
        user_id = user.id
    finally:
        db.close()

    client = TestClient(app)
    r = client.patch(
        f"/api/v1/admin/users/{user_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["approval_status"] == "approved"
    assert body["is_active"] is True

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "approve_me", "password": "pw123456"},
    )
    assert login.status_code == 200


def test_reject_user():
    _pending_user("reject_me", "pw123456")
    token = _su("admin3", "adminpw")

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.username == "reject_me").one()
        user_id = user.id
    finally:
        db.close()

    client = TestClient(app)
    r = client.patch(
        f"/api/v1/admin/users/{user_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["approval_status"] == "rejected"
    assert r.json()["is_active"] is False

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "reject_me", "password": "pw123456"},
    )
    assert login.status_code == 403
    assert login.json()["message"] == REGISTRATION_REJECTED


def test_disable_approved_user():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="disable_me",
                password_hash=auth_service.hash_password("pw123456"),
                is_active=True,
                is_superuser=False,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.commit()
        user_id = db.query(User).filter(User.username == "disable_me").one().id
    finally:
        db.close()

    token = _su("admin4", "adminpw")
    client = TestClient(app)

    r = client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_active": False},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "disable_me", "password": "pw123456"},
    )
    assert login.status_code == 403
    assert login.json()["message"] == USER_DISABLED


def test_reset_password_success():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="reset_me",
                password_hash=auth_service.hash_password("oldpass12"),
                is_active=True,
                is_superuser=False,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.commit()
        user_id = db.query(User).filter(User.username == "reset_me").one().id
    finally:
        db.close()

    token = _su("admin_reset", "adminpw")
    client = TestClient(app)

    r = client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": "newpass99"},
    )
    assert r.status_code == 204

    old_login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset_me", "password": "oldpass12"},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset_me", "password": "newpass99"},
    )
    assert new_login.status_code == 200


def test_reset_password_forbidden_for_non_superuser():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="norm_reset",
                password_hash=auth_service.hash_password("p"),
                is_active=True,
                is_superuser=False,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.add(
            User(
                username="target_user",
                password_hash=auth_service.hash_password("oldpass12"),
                is_active=True,
                is_superuser=False,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.commit()
        target_id = db.query(User).filter(User.username == "target_user").one().id
    finally:
        db.close()

    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"username": "norm_reset", "password": "p"})
    token = r.json()["access_token"]
    r2 = client.post(
        f"/api/v1/admin/users/{target_id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": "newpass99"},
    )
    assert r2.status_code == 403


def test_reset_password_cannot_reset_superuser():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="su_target",
                password_hash=auth_service.hash_password("supass12"),
                is_active=True,
                is_superuser=True,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.commit()
        target_id = db.query(User).filter(User.username == "su_target").one().id
    finally:
        db.close()

    token = _su("admin_su_reset", "adminpw")
    client = TestClient(app)
    r = client.post(
        f"/api/v1/admin/users/{target_id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": "newpass99"},
    )
    assert r.status_code == 400
    assert r.json()["message"] == CANNOT_RESET_SUPERUSER_PASSWORD


def test_reset_password_too_short():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="short_reset",
                password_hash=auth_service.hash_password("oldpass12"),
                is_active=True,
                is_superuser=False,
                approval_status=APPROVAL_APPROVED,
            )
        )
        db.commit()
        user_id = db.query(User).filter(User.username == "short_reset").one().id
    finally:
        db.close()

    token = _su("admin_short", "adminpw")
    client = TestClient(app)
    r = client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_password": "short"},
    )
    assert r.status_code == 422
