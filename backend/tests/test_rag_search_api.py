from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.schemas.knowledge.rag import RagHitOut
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


def _project(token: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "RAG-P"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_rag_search_requires_embedding_key(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "")
    _user("rag_a", "a")
    tok = _login("rag_a", "a")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/rag/search",
        headers={"Authorization": f"Bearer {tok}"},
        json={"query": "q1"},
    )
    assert r.status_code == 400


@patch("app.api.knowledge.rag_search.rag_search_hits")
def test_rag_search_returns_hits(mock_hits):
    mock_hits.return_value = [
        RagHitOut(
            chunk_id=1,
            text="t",
            source_type="requirement",
            source_id=2,
            title_resolved="R",
            score=0.1,
        )
    ]
    _user("rag_b", "b")
    tok = _login("rag_b", "b")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/rag/search",
        headers={"Authorization": f"Bearer {tok}"},
        json={"query": "q1", "top_k": 5},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["chunk_id"] == 1
    mock_hits.assert_called_once()
