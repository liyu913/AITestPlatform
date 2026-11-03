from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def test_create_user_forbidden_for_non_superuser():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="norm_user",
                password_hash=auth_service.hash_password("p"),
                is_active=True,
                is_superuser=False,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "norm_user", "password": "p"},
    )
    token = r.json()["access_token"]
    r2 = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": "new_member",
            "password": "secret123",
            "full_name": "X",
        },
    )
    assert r2.status_code == 403


def test_superuser_creates_user():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="root_user",
                password_hash=auth_service.hash_password("rootpw"),
                is_active=True,
                is_superuser=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "root_user", "password": "rootpw"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]

    r2 = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": "member_user",
            "password": "mempw",
            "full_name": "Member",
        },
    )
    assert r2.status_code == 201
    body = r2.json()
    assert body["username"] == "member_user"
    assert body["is_superuser"] is False
    assert body["approval_status"] == "approved"
    assert body["is_active"] is True
    assert "password" not in body

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "member_user", "password": "mempw"},
    )
    assert login.status_code == 200
