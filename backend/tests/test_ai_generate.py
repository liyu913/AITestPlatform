from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.ai.ai_generate import _inject_requirement_content, _normalize_chat_messages
from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.schemas.ai.ai_gen import AiGenerateRequest
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


def _project(token: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "AI-P"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_generate_requires_llm_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    _user("ai_a", "a")
    tok = _login("ai_a", "a")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={},
    )
    assert r.status_code == 400


@patch("app.api.ai.ai_generate.enqueue_reindex")
@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_and_confirm(mock_rag, mock_llm, mock_rq, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = (
        '{"cases":['
        '{"title":"登录校验","module":"登录","priority":"P1","overall_expected":"无法进入系统",'
        '"case_type":"异常","precondition":" 账号已存在 ","test_data":" 账号 demo 密码 wrong ",'
        '"steps":[{"order":0,"action":"输入错误密码"}]},'
        '{"title":"无模块","module":"  ","priority":"P2","overall_expected":"无",'
        '"case_type":"性能","precondition":"  ","test_data":"  ",'
        '"steps":[{"order":0,"action":"打开页面"}]}'
        "]}"
    )
    _user("ai_b", "b")
    tok = _login("ai_b", "b")
    pid = _project(tok)
    client = TestClient(app)

    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={"user_notes": "覆盖登录"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["cases"]) == 2
    assert body["cases"][0]["title"] == "登录校验"
    assert body["cases"][0]["module"] == "登录"
    assert body["cases"][0]["overall_expected"] == "无法进入系统"
    assert body["cases"][0]["case_type"] == "异常"
    assert body["cases"][0]["precondition"] == "账号已存在"
    assert body["cases"][0]["test_data"] == "账号 demo 密码 wrong"
    assert body["cases"][1]["case_type"] is None
    assert body["cases"][1]["precondition"] is None
    assert body["cases"][1]["test_data"] is None
    assert "已生成" in body.get("assistant_message", "")

    r2 = client.post(
        f"/api/v1/projects/{pid}/ai/confirm-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json=body,
    )
    assert r2.status_code == 201
    saved = r2.json()
    assert len(saved) == 2
    assert saved[0]["title"] == "登录校验"
    assert saved[0]["module"] == "登录"
    assert saved[0]["overall_expected"] == "无法进入系统"
    assert saved[0]["case_type"] == "异常"
    assert saved[0]["precondition"] == "账号已存在"
    assert saved[0]["test_data"] == "账号 demo 密码 wrong"
    assert saved[1]["case_type"] is None
    assert saved[1]["precondition"] is None
    assert saved[1]["test_data"] is None
    assert len(saved[0]["steps"]) == 1
    assert saved[0]["steps"][0]["expected"] == "—"
    assert saved[1]["title"] == "无模块"
    assert saved[1]["module"] is None
    assert mock_rq.call_count == 2
    assert mock_llm.call_count >= 1
    assert mock_llm.call_args_list[0].kwargs.get("model") == settings.llm_model
    assert mock_rag.call_count == 0


def test_llm_options():
    _user("ai_c", "c")
    tok = _login("ai_c", "c")
    pid = _project(tok)
    client = TestClient(app)
    r = client.get(
        f"/api/v1/projects/{pid}/ai/llm-options",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["default_model"]
    assert isinstance(d["models"], list)
    assert d["default_model"] in d["models"]


@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_rejects_invalid_llm_model(mock_rag, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_allowed_models", "gpt-4o-mini,gpt-4o")
    mock_rag.return_value = []
    _user("ai_d", "d")
    tok = _login("ai_d", "d")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={"llm_model": "not-a-real-model"},
    )
    assert r.status_code == 400


@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_with_knowledge_library_passes_upload_filter(mock_rag, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = '{"cases":[]}'
    _user("ai_lib", "x")
    tok = _login("ai_lib", "x")
    pid = _project(tok)
    client = TestClient(app)
    r_libs = client.get(
        f"/api/v1/projects/{pid}/knowledge/libraries",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_libs.status_code == 200
    lib_id = r_libs.json()[0]["id"]

    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={"knowledge_library_id": lib_id},
    )
    assert r.status_code == 200
    assert mock_rag.called
    assert mock_rag.call_args.kwargs.get("upload_document_ids") == []


@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_without_knowledge_library_skips_rag(mock_rag, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = '{"cases":[]}'
    _user("ai_full", "x")
    tok = _login("ai_full", "x")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={},
    )
    assert r.status_code == 200
    assert mock_rag.call_count == 0


@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_uses_requested_llm_model(mock_rag, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_allowed_models", "gpt-4o-mini,gpt-4o")
    mock_rag.return_value = []
    mock_llm.return_value = '{"cases":[]}'
    _user("ai_e", "e")
    tok = _login("ai_e", "e")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={"llm_model": "gpt-4o"},
    )
    assert r.status_code == 200
    assert mock_llm.called
    assert mock_llm.call_args_list[0].kwargs.get("model") == "gpt-4o"


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_stream_sse(mock_rag, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []

    def fake_stream(*_args, **_kwargs):
        yield '{"cases":[{"title":"流式用例","steps":[{"order":0,"action":"a","expected":"e"}]}]}'

    mock_stream.side_effect = fake_stream
    _user("ai_stream", "s")
    tok = _login("ai_stream", "s")
    pid = _project(tok)
    client = TestClient(app)

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={"user_notes": "流式生成"},
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
        body = r.read().decode("utf-8")

    assert '"type": "status"' in body
    assert "等待中" in body
    assert '"type": "done"' in body
    assert "流式用例" in body
    assert mock_stream.called


@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_with_messages_multi_turn(mock_rag, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = '{"cases":[{"title":"边界用例","steps":[{"order":0,"action":"a","expected":"e"}]}]}'
    _user("ai_chat", "c")
    tok = _login("ai_chat", "c")
    pid = _project(tok)
    client = TestClient(app)

    r1 = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "messages": [{"role": "user", "content": "生成登录用例"}],
        },
    )
    assert r1.status_code == 200
    assert r1.json()["assistant_message"]

    r2 = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "messages": [
                {"role": "user", "content": "生成登录用例"},
                {"role": "assistant", "content": "已生成 1 条用例：登录校验"},
                {"role": "user", "content": "再补充边界用例"},
            ],
        },
    )
    assert r2.status_code == 200
    assert mock_llm.call_count == 2
    second_messages = mock_llm.call_args_list[1].args[0]
    assert len(second_messages) > 2
    roles = [m["role"] for m in second_messages]
    assert "assistant" in roles


@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_with_current_drafts_injected(mock_rag, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = (
        '{"cases":[{"title":"登录校验","priority":"P0","overall_expected":"无法进入",'
        '"steps":[{"order":0,"action":"输入错误密码"}]}]}'
    )
    _user("ai_drafts", "d")
    tok = _login("ai_drafts", "d")
    pid = _project(tok)
    client = TestClient(app)

    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "messages": [
                {"role": "user", "content": "生成登录用例"},
                {"role": "assistant", "content": "已生成 1 条用例：登录校验"},
                {"role": "user", "content": "把优先级改成 P0"},
            ],
            "current_drafts": [
                {
                    "title": "登录校验",
                    "priority": "P2",
                    "overall_expected": "无法进入",
                    "steps": [{"order": 0, "action": "输入错误密码"}],
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cases"][0]["priority"] == "P0"
    assert "已更新" in body["assistant_message"]
    llm_messages = mock_llm.call_args_list[0].args[0]
    joined = "\n".join(m["content"] for m in llm_messages)
    assert "当前用例草稿" in joined
    assert "登录校验" in joined
    assert "完整 cases" in joined


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_fast_mode_stream_unchanged(mock_rag, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []

    def fake_stream(*_args, **_kwargs):
        yield '{"cases":[{"title":"快速用例","overall_expected":"ok","steps":[{"order":0,"action":"a"}]}]}'

    mock_stream.side_effect = fake_stream
    _user("ai_fast", "f")
    tok = _login("ai_fast", "f")
    pid = _project(tok)
    client = TestClient(app)

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={"user_notes": "快速生成"},
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "done"' in body
    assert "快速用例" in body
    assert mock_stream.call_count == 1


@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_agent_clarification_stops_early(mock_rag, mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = (
        '{"is_clear": false, "summary": "", "ambiguities": ["缺少锁定规则"], '
        '"questions": ["连续失败几次锁定账号？"]}'
    )
    _user("ai_clar", "c")
    tok = _login("ai_clar", "c")
    pid = _project(tok)
    client = TestClient(app)

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={"user_notes": "登录测试", "generate_mode": "agent"},
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "clarification"' in body
    assert "连续失败几次锁定账号" in body
    assert '"type": "done"' not in body
    assert mock_llm.call_count == 1


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_agent_full_pipeline(mock_rag, mock_llm, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.side_effect = [
        '{"is_clear": true, "summary": "登录功能测试", "ambiguities": [], "questions": []}',
        '{"test_points": [{"id": "TP-01", "title": "正常登录", "category": "功能", "priority_hint": "P1"}]}',
        '{"summary": "已去冗余", "issues": [], "cases": [{"title": "Agent用例", "overall_expected": "登录成功", "priority": "P1", "steps": [{"order": 0, "action": "输入账号密码"}]}]}',
    ]

    def fake_stream(*_args, **_kwargs):
        yield (
            '{"cases":[{"title":"Agent用例","overall_expected":"登录成功","priority":"P1",'
            '"steps":[{"order":0,"action":"输入账号密码"}]}]}'
        )

    mock_stream.side_effect = fake_stream
    _user("ai_agent", "a")
    tok = _login("ai_agent", "a")
    pid = _project(tok)
    client = TestClient(app)

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={"user_notes": "生成登录用例", "generate_mode": "agent"},
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "analysis_done"' in body
    assert '"type": "test_points"' in body
    assert '"type": "await_test_points_confirm"' in body
    assert '"type": "done"' not in body
    assert mock_llm.call_count == 2
    assert mock_stream.call_count == 0


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_agent_resume_after_test_points_confirm(mock_rag, mock_llm, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = (
        '{"summary": "已去冗余", "issues": [], "cases": [{"title": "Agent用例", "overall_expected": "登录成功", "priority": "P1", "steps": [{"order": 0, "action": "输入账号密码"}]}]}'
    )

    def fake_stream(*_args, **_kwargs):
        yield (
            '{"cases":[{"title":"Agent用例","overall_expected":"登录成功","priority":"P1",'
            '"steps":[{"order":0,"action":"输入账号密码"}]}]}'
        )

    mock_stream.side_effect = fake_stream
    _user("ai_resume", "a")
    tok = _login("ai_resume", "a")
    pid = _project(tok)
    client = TestClient(app)

    artifacts = {
        "requirement_analysis": {
            "is_clear": True,
            "summary": "登录功能测试",
            "ambiguities": [],
            "questions": [],
        },
        "test_points": [
            {"id": "TP-01", "title": "正常登录", "category": "功能", "priority_hint": "P1"}
        ],
    }

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "user_notes": "生成登录用例",
            "generate_mode": "agent",
            "agent_resume_from": "cases",
            "agent_artifacts": artifacts,
            "enable_case_review": True,
            "review_dimensions": [
                {"key": "redundancy", "weight": 5},
                {"key": "coverage", "weight": 4},
            ],
        },
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "review"' in body
    assert '"type": "done"' in body
    assert "Agent用例" in body
    assert "已去冗余" in body
    assert mock_llm.call_count == 1
    assert mock_stream.call_count == 1
    review_system = mock_llm.call_args_list[0].args[0][0]["content"]
    assert "redundancy" in review_system
    assert "覆盖完整性" in review_system
    assert "boundary" not in review_system
    assert "边界与异常" not in review_system


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_agent_resume_skips_review_when_disabled(mock_rag, mock_llm, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []

    def fake_stream(*_args, **_kwargs):
        yield (
            '{"cases":[{"title":"Agent用例","overall_expected":"登录成功","priority":"P1",'
            '"steps":[{"order":0,"action":"输入账号密码"}]}]}'
        )

    mock_stream.side_effect = fake_stream
    _user("ai_norev", "a")
    tok = _login("ai_norev", "a")
    pid = _project(tok)
    client = TestClient(app)

    artifacts = {
        "requirement_analysis": {
            "is_clear": True,
            "summary": "登录功能测试",
            "ambiguities": [],
            "questions": [],
        },
        "test_points": [
            {"id": "TP-01", "title": "正常登录", "category": "功能", "priority_hint": "P1"}
        ],
    }

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "user_notes": "生成登录用例",
            "generate_mode": "agent",
            "agent_resume_from": "cases",
            "agent_artifacts": artifacts,
            "enable_case_review": False,
        },
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "review"' not in body
    assert '"type": "done"' in body
    assert "Agent用例" in body
    assert mock_llm.call_count == 0
    assert mock_stream.call_count == 1


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_fast_stream_with_case_review(mock_rag, mock_llm, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = (
        '{"summary": "边界不足", "issues": [{"case_title": "快速用例", "issue_type": "boundary", '
        '"detail": "缺空密码"}], "dimension_scores": {"boundary": 2, "clarity": 4}, '
        '"cases": [{"title": "快速用例-修订", "overall_expected": "ok", '
        '"steps": [{"order": 0, "action": "a"}]}]}'
    )

    def fake_stream(*_args, **_kwargs):
        yield '{"cases":[{"title":"快速用例","overall_expected":"ok","steps":[{"order":0,"action":"a"}]}]}'

    mock_stream.side_effect = fake_stream
    _user("ai_frev", "f")
    tok = _login("ai_frev", "f")
    pid = _project(tok)
    client = TestClient(app)

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "user_notes": "快速生成",
            "enable_case_review": True,
            "review_dimensions": [
                {"key": "boundary", "weight": 5},
                {"key": "clarity", "weight": 3},
            ],
        },
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "review"' in body
    assert '"type": "done"' in body
    assert "快速用例-修订" in body
    assert "边界不足" in body
    assert mock_stream.call_count == 1
    assert mock_llm.call_count == 1
    review_system = mock_llm.call_args_list[0].args[0][0]["content"]
    assert "boundary" in review_system
    assert "边界与异常" in review_system
    assert "覆盖完整性" not in review_system


@patch("app.api.ai.ai_generate.llm.chat_complete_stream")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_fast_stream_without_review_has_no_review_event(mock_rag, mock_stream, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []

    def fake_stream(*_args, **_kwargs):
        yield '{"cases":[{"title":"快速用例","overall_expected":"ok","steps":[{"order":0,"action":"a"}]}]}'

    mock_stream.side_effect = fake_stream
    _user("ai_fnorev", "f")
    tok = _login("ai_fnorev", "f")
    pid = _project(tok)
    client = TestClient(app)

    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ai/generate-testcases/stream",
        headers={"Authorization": f"Bearer {tok}"},
        json={"user_notes": "快速生成", "enable_case_review": False},
    ) as r:
        body = r.read().decode("utf-8")

    assert '"type": "review"' not in body
    assert '"type": "done"' in body
    assert mock_stream.call_count == 1


def test_normalize_review_dims_defaults():
    from app.services.ai.case_review_dims import REVIEW_DIMENSIONS, normalize_review_dims

    assert normalize_review_dims(None, enable=False) == []
    all_dims = normalize_review_dims([], enable=True)
    assert len(all_dims) == len(REVIEW_DIMENSIONS)
    assert {d.key for d in all_dims} == {d.key for d in REVIEW_DIMENSIONS}


def test_normalize_messages_with_requirement_only():
    body = AiGenerateRequest(requirement_id=42, messages=[])
    msgs = _normalize_chat_messages(body)
    assert len(msgs) == 1
    assert msgs[0].content == "根据所选需求文档生成测试用例"


def test_inject_requirement_content():
    from app.models.project.project import Project
    from app.models.functional.requirement import Requirement

    db = TestingSessionLocal()
    try:
        u = User(username="inj_req", password_hash="x", is_active=True)
        db.add(u)
        db.flush()
        p = Project(name="p", description="", owner_id=u.id)
        db.add(p)
        db.flush()
        r = Requirement(project_id=p.id, title="登录", body_md="用户输入账号密码登录")
        db.add(r)
        db.commit()

        body = AiGenerateRequest(requirement_id=r.id, messages=[])
        normalized = _normalize_chat_messages(body)
        enriched = _inject_requirement_content(db, p.id, body, normalized)
        assert "登录" in enriched[0].content
        assert "账号密码" in enriched[0].content
    finally:
        db.close()


@patch("app.api.functional.requirements.enqueue_reindex")
@patch("app.api.ai.ai_generate.llm.chat_complete")
@patch("app.api.ai.ai_generate.rag_search_hits")
def test_generate_with_requirement_only_no_messages(mock_rag, mock_llm, _mock_rq, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    mock_rag.return_value = []
    mock_llm.return_value = (
        '{"cases":[{"title":"需求用例","overall_expected":"符合需求","priority":"P1",'
        '"steps":[{"order":0,"action":"执行步骤"}]}]}'
    )
    _user("ai_req", "r")
    tok = _login("ai_req", "r")
    pid = _project(tok)
    client = TestClient(app)

    r_req = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "登录需求", "body_md": "用户可使用账号密码登录"},
    )
    assert r_req.status_code == 201
    rid = r_req.json()["id"]

    r = client.post(
        f"/api/v1/projects/{pid}/ai/generate-testcases",
        headers={"Authorization": f"Bearer {tok}"},
        json={"requirement_id": rid, "messages": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["cases"]) == 1
    assert body["cases"][0]["title"] == "需求用例"
    assert mock_llm.called
