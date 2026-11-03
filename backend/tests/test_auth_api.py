import base64
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.main import app
from app.models.identity.user import APPROVAL_PENDING, User
from app.user_messages import ACCOUNT_PENDING_APPROVAL, REGISTER_PENDING_MESSAGE
from app.services.identity import auth_service
from app.services.identity.captcha_code import (
    _ALPHABET,
    _HEIGHT,
    _WIDTH,
    peek_answer_pytest_only,
)
from tests.conftest import TestingSessionLocal


def test_login_unknown_user():
    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"username": "user_a", "password": "x"})
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthorized"


def test_login_inactive():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="inactive_user",
                password_hash=auth_service.hash_password("x"),
                is_active=False,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "inactive_user", "password": "x"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "inactive"


def test_login_and_refresh():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="ok_user",
                password_hash=auth_service.hash_password("secret123"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "ok_user", "password": "secret123"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["refresh_token"]

    r2 = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": data["refresh_token"]},
    )
    assert r2.status_code == 200
    assert r2.json()["access_token"]


def test_captcha_endpoint():
    client = TestClient(app)
    r = client.get("/api/v1/auth/captcha")
    assert r.status_code == 200
    d = r.json()
    assert d["captcha_id"]
    assert d["image_base64"]


def test_captcha_png_size_and_alphabet():
    client = TestClient(app)
    r = client.get("/api/v1/auth/captcha")
    assert r.status_code == 200
    d = r.json()
    raw = base64.standard_b64decode(d["image_base64"])
    img = Image.open(BytesIO(raw))
    assert img.format == "PNG"
    assert img.size == (_WIDTH, _HEIGHT)
    code = peek_answer_pytest_only(d["captcha_id"])
    assert code is not None
    assert len(code) == 4
    assert all(ch in _ALPHABET for ch in code)


def test_login_fails_wrong_captcha_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_login_captcha", True)
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="cap_user",
                password_hash=auth_service.hash_password("secret123"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    cap = client.get("/api/v1/auth/captcha")
    cid = cap.json()["captcha_id"]
    r = client.post(
        "/api/v1/auth/login",
        json={
            "username": "cap_user",
            "password": "secret123",
            "captcha_id": cid,
            "captcha_code": "zzzz",
        },
    )
    assert r.status_code == 400
    assert r.json()["message"] == "验证码错误或已过期"


def test_login_ok_with_captcha_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_login_captcha", True)
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="cap2_user",
                password_hash=auth_service.hash_password("secret123"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    cap = client.get("/api/v1/auth/captcha")
    assert cap.status_code == 200
    cid = cap.json()["captcha_id"]
    code = peek_answer_pytest_only(cid)
    assert code
    r = client.post(
        "/api/v1/auth/login",
        json={
            "username": "cap2_user",
            "password": "secret123",
            "captcha_id": cid,
            "captcha_code": code,
        },
    )
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_register_options():
    client = TestClient(app)
    r = client.get("/api/v1/auth/register-options")
    assert r.status_code == 200
    d = r.json()
    assert d["allowed"] is True
    assert "require_captcha" in d


def test_register_success_pending_no_token():
    client = TestClient(app)
    username = "newuser"
    r = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123", "full_name": "New"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["message"] == REGISTER_PENDING_MESSAGE
    assert "access_token" not in data

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        assert user.approval_status == APPROVAL_PENDING
        assert user.is_active is False
        assert user.full_name == "New"
    finally:
        db.close()

    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert login.status_code == 403
    assert login.json()["message"] == ACCOUNT_PENDING_APPROVAL


def test_register_duplicate_username():
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username="dup_user",
                password_hash=auth_service.hash_password("password123"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/register",
        json={"username": "dup_user", "password": "password123"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


def test_register_disabled(monkeypatch):
    monkeypatch.setattr(settings, "allow_open_registration", False)
    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/register",
        json={"username": "blocked_user", "password": "password123"},
    )
    assert r.status_code == 403


def test_register_fails_wrong_captcha_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_login_captcha", True)
    client = TestClient(app)
    cap = client.get("/api/v1/auth/captcha")
    cid = cap.json()["captcha_id"]
    r = client.post(
        "/api/v1/auth/register",
        json={
            "username": "capreg_user",
            "password": "password123",
            "captcha_id": cid,
            "captcha_code": "zzzz",
        },
    )
    assert r.status_code == 400


def test_register_ok_with_captcha_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_login_captcha", True)
    client = TestClient(app)
    cap = client.get("/api/v1/auth/captcha")
    cid = cap.json()["captcha_id"]
    code = peek_answer_pytest_only(cid)
    assert code
    r = client.post(
        "/api/v1/auth/register",
        json={
            "username": "capreg2_user",
            "password": "password123",
            "captcha_id": cid,
            "captcha_code": code,
        },
    )
    assert r.status_code == 201
    assert r.json()["message"] == REGISTER_PENDING_MESSAGE


def _create_user_and_login(username: str, password: str) -> tuple[TestClient, str]:
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password(password),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return client, r.json()["access_token"]


def test_change_password_unauthorized():
    client = TestClient(app)
    r = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "old12345", "new_password": "new123456"},
    )
    assert r.status_code == 401


def test_change_password_wrong_current():
    client, token = _create_user_and_login("chpw_wrong", "secret123")
    r = client.post(
        "/api/v1/users/me/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "wrongpass", "new_password": "newsecret1"},
    )
    assert r.status_code == 400
    assert r.json()["message"] == "当前密码错误"


def test_change_password_new_too_short():
    client, token = _create_user_and_login("chpw_short", "secret123")
    r = client.post(
        "/api/v1/users/me/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "secret123", "new_password": "short"},
    )
    assert r.status_code == 422


def test_change_password_success():
    username = "chpw_ok"
    client, token = _create_user_and_login(username, "secret123")
    r = client.post(
        "/api/v1/users/me/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "secret123", "new_password": "newsecret1"},
    )
    assert r.status_code == 204

    r_old = client.post("/api/v1/auth/login", json={"username": username, "password": "secret123"})
    assert r_old.status_code == 401

    r_new = client.post("/api/v1/auth/login", json={"username": username, "password": "newsecret1"})
    assert r_new.status_code == 200
