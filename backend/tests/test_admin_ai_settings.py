from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.ai.system_ai_settings import SystemEmbeddingProfile, SystemLlmProfile
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.ai.effective_ai_settings import get_embedding_runtime, get_llm_runtime, invalidate_runtime_cache
from tests.conftest import TestingSessionLocal


def _su(username: str, pw: str):
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password(pw),
                is_active=True,
                is_superuser=True,
            )
        )
        db.commit()
    finally:
        db.close()


def _user(username: str, pw: str):
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password(pw),
                is_active=True,
                is_superuser=False,
            )
        )
        db.commit()
    finally:
        db.close()


def _tok(username: str, pw: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _patch_env(monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    monkeypatch.setattr(settings, "llm_api_base", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "llm_allowed_models", "")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "embedding_api_base", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    monkeypatch.setattr(settings, "embedding_dim", 1536)
    monkeypatch.setattr(settings, "embedding_api_key", "")
    invalidate_runtime_cache()


def test_admin_ai_profiles_forbidden_for_normal_user():
    _user("adm_norm", "p")
    tok = _tok("adm_norm", "p")
    c = TestClient(app)
    r = c.get("/api/v1/admin/ai-settings/llm-profiles", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    r2 = c.get(
        "/api/v1/admin/ai-settings/embedding-profiles",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.status_code == 403


def test_llm_profiles_crud_and_activate(monkeypatch):
    _patch_env(monkeypatch)
    _su("adm_su", "s")
    tok = _tok("adm_su", "s")
    h = {"Authorization": f"Bearer {tok}"}
    c = TestClient(app)

    r = c.get("/api/v1/admin/ai-settings/llm-profiles", headers=h)
    assert r.status_code == 200
    assert r.json() == []

    r1 = c.post(
        "/api/v1/admin/ai-settings/llm-profiles",
        headers=h,
        json={
            "name": "DeepSeek",
            "api_base": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "allowed_models": "deepseek-chat,deepseek-reasoner",
            "api_key": "sk-llm-1",
        },
    )
    assert r1.status_code == 200
    p1 = r1.json()
    assert p1["is_active"] is True
    assert p1["has_api_key"] is True
    assert p1["api_key"] == "sk-llm-1"

    r2 = c.post(
        "/api/v1/admin/ai-settings/llm-profiles",
        headers=h,
        json={
            "name": "OpenAI",
            "api_base": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "sk-llm-2",
        },
    )
    assert r2.status_code == 200
    p2 = r2.json()
    assert p2["is_active"] is False

    db = TestingSessionLocal()
    try:
        rt = get_llm_runtime(db)
        assert rt.api_base == "https://api.deepseek.com/v1"
        assert rt.api_key == "sk-llm-1"
        assert rt.model == "deepseek-chat"
    finally:
        db.close()

    act = c.post(f"/api/v1/admin/ai-settings/llm-profiles/{p2['id']}/activate", headers=h)
    assert act.status_code == 200
    assert act.json()["is_active"] is True

    listed = c.get("/api/v1/admin/ai-settings/llm-profiles", headers=h).json()
    by_id = {x["id"]: x for x in listed}
    assert by_id[p1["id"]]["is_active"] is False
    assert by_id[p2["id"]]["is_active"] is True

    invalidate_runtime_cache()
    db = TestingSessionLocal()
    try:
        rt = get_llm_runtime(db)
        assert rt.api_key == "sk-llm-2"
        assert rt.model == "gpt-4o-mini"
    finally:
        db.close()

    bad_del = c.delete(f"/api/v1/admin/ai-settings/llm-profiles/{p2['id']}", headers=h)
    assert bad_del.status_code == 400

    ok_del = c.delete(f"/api/v1/admin/ai-settings/llm-profiles/{p1['id']}", headers=h)
    assert ok_del.status_code == 204

    db = TestingSessionLocal()
    try:
        row = db.get(SystemLlmProfile, p1["id"])
        assert row is None
    finally:
        db.close()


def test_embedding_profiles_crud_and_activate(monkeypatch):
    _patch_env(monkeypatch)
    _su("adm_emb", "s")
    tok = _tok("adm_emb", "s")
    h = {"Authorization": f"Bearer {tok}"}
    c = TestClient(app)

    r1 = c.post(
        "/api/v1/admin/ai-settings/embedding-profiles",
        headers=h,
        json={
            "name": "DashScope",
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "text-embedding-v2",
            "dim": 1536,
            "api_key": "sk-emb-1",
        },
    )
    assert r1.status_code == 200
    p1 = r1.json()
    assert p1["is_active"] is True
    assert p1["dim"] == 1536

    r2 = c.post(
        "/api/v1/admin/ai-settings/embedding-profiles",
        headers=h,
        json={
            "name": "Zhipu",
            "api_base": "https://open.bigmodel.cn/api/paas/v4",
            "model": "embedding-3",
            "dim": 2048,
            "api_key": "sk-emb-2",
        },
    )
    assert r2.status_code == 200
    p2 = r2.json()
    assert p2["is_active"] is False

    act = c.post(f"/api/v1/admin/ai-settings/embedding-profiles/{p2['id']}/activate", headers=h)
    assert act.status_code == 200
    assert act.json()["is_active"] is True
    assert act.json()["warning"]

    invalidate_runtime_cache()
    db = TestingSessionLocal()
    try:
        rt = get_embedding_runtime(db)
        assert rt.dim == 2048
        assert rt.api_key == "sk-emb-2"
        assert rt.model == "embedding-3"
        row = db.get(SystemEmbeddingProfile, p2["id"])
        assert row is not None
        assert row.is_active is True
    finally:
        db.close()

    bad_del = c.delete(f"/api/v1/admin/ai-settings/embedding-profiles/{p2['id']}", headers=h)
    assert bad_del.status_code == 400


def test_users_me():
    _user("me_user", "x")
    tok = _tok("me_user", "x")
    r = TestClient(app).get("/api/v1/users/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["username"] == "me_user"
    assert r.json()["is_superuser"] is False
