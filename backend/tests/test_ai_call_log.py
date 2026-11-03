from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import app
from app.models.ai.ai_call_log import AiCallLog
from app.models.identity.user import User
from app.services.ai.call_context import user_id_var
from app.services.ai.call_log import TEXT_LIMIT, set_session_factory
from app.services.ai.effective_ai_settings import EmbeddingRuntime, LlmRuntime
from app.services.ai.embedding import embed_texts
from app.services.ai.llm import chat_complete, chat_complete_stream
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal

_RealClient = httpx.Client

_CFG = LlmRuntime(
    api_base="https://llm.test/v1",
    api_key="sk-test",
    model="demo-model",
    allowed_models_csv="",
)


def _client_factory(transport: httpx.MockTransport):
    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _RealClient(*args, **kwargs)

    return factory


@pytest.fixture(autouse=True)
def _wipe_ai_call_logs():
    db = TestingSessionLocal()
    try:
        db.query(AiCallLog).delete()
        db.commit()
    finally:
        db.close()
    yield


def _rows() -> list[AiCallLog]:
    db = TestingSessionLocal()
    try:
        return db.query(AiCallLog).order_by(AiCallLog.id).all()
    finally:
        db.close()


def test_chat_complete_writes_success_log():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "  hello  "}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    db = TestingSessionLocal()
    try:
        user = User(
            username="ai_log_caller",
            password_hash=auth_service.hash_password("p"),
            is_active=True,
            is_superuser=False,
        )
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()

    token = user_id_var.set(user_id)
    try:
        with patch("app.services.ai.llm.httpx.Client", side_effect=_client_factory(httpx.MockTransport(handler))):
            text = chat_complete(
                [{"role": "user", "content": "hi"}],
                cfg=_CFG,
                scene="需求分析",
            )
    finally:
        user_id_var.reset(token)

    assert text == "hello"
    row = _rows()[-1]
    assert row.kind == "chat"
    assert row.scene == "需求分析"
    assert row.model == "demo-model"
    assert row.status == "success"
    assert row.http_status == 200
    assert row.prompt_tokens == 3
    assert row.completion_tokens == 1
    assert row.total_tokens == 4
    assert row.user_id == user_id
    assert row.response_text == "hello"
    assert "hi" in (row.request_text or "")
    assert "sk-test" not in (row.request_text or "")


def test_chat_complete_writes_error_log():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "nope"})

    with patch("app.services.ai.llm.httpx.Client", side_effect=_client_factory(httpx.MockTransport(handler))):
        with pytest.raises(AppError):
            chat_complete([{"role": "user", "content": "hi"}], cfg=_CFG, scene="用例生成")

    row = _rows()[-1]
    assert row.status == "error"
    assert row.http_status == 500
    assert row.kind == "chat"
    assert "500" in (row.error_message or "")


def test_request_text_truncated_and_images_redacted():
    huge = "A" * 9000
    image = "data:image/png;base64," + ("B" * 4000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image}},
                {"type": "text", "text": huge},
            ],
        }
    ]
    with patch("app.services.ai.llm.httpx.Client", side_effect=_client_factory(httpx.MockTransport(handler))):
        chat_complete(messages, cfg=_CFG, scene="需求分析")

    row = _rows()[-1]
    assert row.request_text is not None
    assert len(row.request_text) <= TEXT_LIMIT
    assert row.request_text.endswith("…")
    assert "[图片]" in row.request_text
    assert "data:image" not in row.request_text
    assert "BBBB" not in row.request_text


def test_log_write_failure_does_not_break_call():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    def boom():
        raise RuntimeError("db down")

    set_session_factory(boom)
    try:
        with patch("app.services.ai.llm.httpx.Client", side_effect=_client_factory(httpx.MockTransport(handler))):
            assert chat_complete([{"role": "user", "content": "hi"}], cfg=_CFG) == "ok"
    finally:
        set_session_factory(TestingSessionLocal)


def test_stream_writes_one_log():
    def handler(request: httpx.Request) -> httpx.Response:
        body = 'data: {"choices":[{"delta":{"content":"ab"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    with patch("app.services.ai.llm.httpx.Client", side_effect=_client_factory(httpx.MockTransport(handler))):
        parts = list(
            chat_complete_stream(
                [{"role": "user", "content": "q"}],
                cfg=_CFG,
                scene="用例生成",
            )
        )

    assert parts == ["ab"]
    row = _rows()[-1]
    assert row.kind == "chat_stream"
    assert row.status == "success"
    assert row.scene == "用例生成"
    assert row.response_text == "ab"


def test_embedding_log_omits_vectors():
    runtime = EmbeddingRuntime(
        api_base="https://emb.test/v1",
        api_key="ek-secret",
        model="emb-1",
        dim=2,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    db = TestingSessionLocal()
    try:
        with (
            patch("app.services.ai.embedding.get_embedding_runtime", return_value=runtime),
            patch(
                "app.services.ai.embedding.httpx.Client",
                side_effect=_client_factory(httpx.MockTransport(handler)),
            ),
        ):
            vectors = embed_texts(["hello"], db, scene="知识库向量化")
    finally:
        db.close()

    assert vectors == [[0.1, 0.2]]
    row = _rows()[-1]
    assert row.kind == "embedding"
    assert row.scene == "知识库向量化"
    assert row.status == "success"
    assert row.response_text == "条数=1 维度=2"
    assert "0.1" not in (row.response_text or "")
    assert "ek-secret" not in (row.request_text or "")


def _user(username: str, *, superuser: bool) -> None:
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password("p"),
                is_active=True,
                is_superuser=superuser,
            )
        )
        db.commit()
    finally:
        db.close()


def _tok(username: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": "p"})
    assert r.status_code == 200
    return r.json()["access_token"]


def test_list_requires_superuser_and_hides_body():
    _user("ai_log_norm", superuser=False)
    _user("ai_log_su", superuser=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "body-text"}}]})

    with patch("app.services.ai.llm.httpx.Client", side_effect=_client_factory(httpx.MockTransport(handler))):
        chat_complete([{"role": "user", "content": "prompt-text"}], cfg=_CFG, scene="需求分析")
        chat_complete([{"role": "user", "content": "other"}], cfg=_CFG, scene="用例生成")

    c = TestClient(app)
    denied = c.get(
        "/api/v1/admin/ai-call-logs",
        headers={"Authorization": f"Bearer {_tok('ai_log_norm')}"},
    )
    assert denied.status_code == 403

    headers = {"Authorization": f"Bearer {_tok('ai_log_su')}"}
    listed = c.get("/api/v1/admin/ai-call-logs", headers=headers)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] >= 2
    assert "request_text" not in payload["items"][0]
    assert "response_text" not in payload["items"][0]

    filtered = c.get("/api/v1/admin/ai-call-logs", params={"scene": "需求分析"}, headers=headers)
    assert filtered.status_code == 200
    scenes = {item["scene"] for item in filtered.json()["items"]}
    assert scenes == {"需求分析"}

    log_id = filtered.json()["items"][0]["id"]
    detail = c.get(f"/api/v1/admin/ai-call-logs/{log_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert "prompt-text" in body["request_text"]
    assert body["response_text"] == "body-text"
