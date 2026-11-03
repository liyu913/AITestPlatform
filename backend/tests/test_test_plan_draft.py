import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.functional.requirement import Requirement
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


def _project(token: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "PlanDraftProj"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _markdown() -> str:
    return (
        "# 测试计划：登录\n\n"
        "## 目标与范围\n\n"
        "验证登录主路径。补充说明\n\n"
        "## 策略与优先级\n\n"
        "| 模块/能力 | 优先级 | 覆盖深度 | 说明 |\n"
        "|-----------|--------|----------|------|\n"
        "| 账号登录 | P0 | 深 | 主流程 |\n"
    )


def _revised_markdown() -> str:
    return "# 测试计划：登录\n\n## 目标与范围\n\n修订后的目标。其他说明\n"


def _set_analysis(requirement_id: int) -> None:
    db = TestingSessionLocal()
    try:
        row = db.get(Requirement, requirement_id)
        assert row is not None
        row.analysis_json = {
            "features": [
                {
                    "module": "登录",
                    "items": [{"name": "账号密码登录", "detail": "用户登录"}],
                }
            ],
            "ui": {
                "summary": "登录页",
                "pages": [
                    {
                        "name": "登录页",
                        "description": "账号密码输入",
                        "areas": ["账号", "密码"],
                    }
                ],
            },
            "unclear": [],
        }
        db.commit()
    finally:
        db.close()


@patch(
    "app.services.functional.requirement_content.requirement_images_for_ai",
    return_value=[("image/png", b"png")],
)
@patch("app.services.functional.test_plan_draft.chat_complete")
def test_draft_with_analysis_persists_without_images(mock_llm, _mock_images):
    mock_llm.side_effect = lambda messages, **_kwargs: (
        setattr(mock_llm, "messages", messages) or f"```markdown\n{_markdown()}\n```"
    )
    _user("plan_draft_an", "a")
    tok = _login("plan_draft_an", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录需求", "body_md": "登录需要账号和密码", "status": "active"},
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    _set_analysis(rid)

    drafted = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft",
        headers=h,
    )
    assert drafted.status_code == 200
    body = drafted.json()
    assert body["body_md"] == _markdown().strip()
    assert "账号登录" in body["body_md"]
    assert body["linked_plans"] == []
    assert body["drafted_at"]
    assert "尚未完成需求分析" not in body["body_md"]
    user_content = mock_llm.messages[1]["content"]
    assert isinstance(user_content, str)
    assert "账号密码登录" in user_content
    assert '"module": "登录"' in user_content or '"module":"登录"' in user_content
    assert "据此定范围、优先级和风险" in user_content
    assert "image_url" not in user_content
    assert mock_llm.call_args.kwargs.get("json_mode") is False

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.status_code == 200
    assert got.json()["plan_draft_md"] == _markdown().strip()
    assert got.json()["plan_draft_at"]
    assert body["messages"] == [{"role": "assistant", "content": "已生成草稿，可直接说明修改意见。"}]
    assert got.json()["plan_draft_messages_json"] == body["messages"]


@patch(
    "app.services.functional.requirement_content.requirement_images_for_ai",
    return_value=[("image/png", b"png")],
)
@patch("app.services.functional.test_plan_draft.chat_complete")
def test_draft_without_analysis_sends_images_and_notes_risk(mock_llm, _mock_images):
    mock_llm.side_effect = lambda messages, **_kwargs: (
        setattr(mock_llm, "messages", messages) or _markdown()
    )
    _user("plan_draft_no_an", "a")
    tok = _login("plan_draft_no_an", "a")
    pid = _project(tok)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=_auth(tok),
        json={"title": "登录需求", "body_md": "登录需要账号和密码", "status": "active"},
    )
    rid = created.json()["id"]
    drafted = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft",
        headers=_auth(tok),
    )
    assert drafted.status_code == 200
    assert "尚未完成需求分析" in drafted.json()["body_md"]
    user_content = mock_llm.messages[1]["content"]
    assert isinstance(user_content, list)
    assert any(part.get("type") == "image_url" for part in user_content)
    assert "尚未完成需求分析" in user_content[0]["text"]


@patch("app.services.functional.test_plan_draft.chat_complete")
def test_draft_empty_requirement_does_not_call_model(mock_llm):
    _user("plan_draft_empty", "a")
    tok = _login("plan_draft_empty", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "空需求", "body_md": "临时正文", "status": "draft"},
    )
    rid = created.json()["id"]
    db = TestingSessionLocal()
    try:
        row = db.get(Requirement, rid)
        assert row is not None
        row.body_md = ""
        db.commit()
    finally:
        db.close()

    drafted = client.post(f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft", headers=h)
    assert drafted.status_code == 400
    assert drafted.json()["message"] == "需求正文和图片都为空，无法生成测试计划"
    mock_llm.assert_not_called()


@patch("app.services.functional.test_plan_draft.chat_complete")
def test_draft_empty_markdown_retries_then_fails(mock_llm):
    mock_llm.side_effect = ["", "```markdown\n```"]
    _user("plan_draft_bad", "a")
    tok = _login("plan_draft_bad", "a")
    pid = _project(tok)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=_auth(tok),
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    drafted = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft",
        headers=_auth(tok),
    )
    assert drafted.status_code == 400
    assert drafted.json()["message"] == "测试计划草稿无法解析，请重试"
    assert mock_llm.call_count == 2


@patch("app.services.functional.test_plan_draft.chat_complete")
def test_accept_creates_plan_and_update_keeps_cycles(mock_llm):
    mock_llm.return_value = _markdown()
    _user("plan_draft_ok", "a")
    tok = _login("plan_draft_ok", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录需求", "body_md": "登录需要账号和密码", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    drafted = client.post(f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft", headers=h)
    assert drafted.status_code == 200

    accepted = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft/accept",
        headers=h,
        json={
            "mode": "create",
            "body_md": _markdown(),
            "name": "测试计划：登录",
            "version": "1.0",
            "iteration": "Sprint-1",
        },
    )
    assert accepted.status_code == 200
    plan = accepted.json()
    assert plan["requirement_id"] == rid
    assert plan["requirement_title"] == "登录需求"
    assert plan["status"] == "draft"
    assert plan["name"] == "测试计划：登录"
    assert plan["description"] == "验证登录主路径"
    assert "账号登录" in plan["body_md"]
    assert plan["body_md"] == _markdown().strip()
    plan_id = plan["id"]

    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan_id}/cycles/",
        headers=h,
        json={"name": "第1轮", "status": "active"},
    )
    assert cycle.status_code == 201
    cycle_id = cycle.json()["id"]

    updated = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft/accept",
        headers=h,
        json={"mode": "update", "plan_id": plan_id, "body_md": _revised_markdown(), "name": "测试计划：登录"},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == plan_id
    assert updated.json()["description"] == "修订后的目标"
    assert updated.json()["body_md"] == _revised_markdown().strip()
    assert updated.json()["requirement_id"] == rid

    cycles = client.get(f"/api/v1/projects/{pid}/test-plans/{plan_id}/cycles/", headers=h)
    assert cycles.status_code == 200
    assert [row["id"] for row in cycles.json()] == [cycle_id]

    missing = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft/accept",
        headers=h,
        json={"mode": "update", "body_md": _markdown()},
    )
    assert missing.status_code == 400


def test_manual_plan_create_leaves_body_empty():
    _user("plan_draft_manual", "a")
    tok = _login("plan_draft_manual", "a")
    pid = _project(tok)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/projects/{pid}/test-plans/",
        headers=_auth(tok),
        json={"name": "手工计划", "status": "active"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["body_md"] is None
    assert body["requirement_id"] is None
    assert body["requirement_title"] is None


@patch("app.services.functional.test_plan_draft.chat_complete")
def test_chat_revises_draft_and_persists_messages(mock_llm):
    mock_llm.side_effect = [
        _markdown(),
        json.dumps(
            {"reply": "已压缩人天表", "body_md": _revised_markdown()},
            ensure_ascii=False,
        ),
    ]
    _user("plan_draft_chat", "a")
    tok = _login("plan_draft_chat", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录需求", "body_md": "登录需要账号和密码", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    drafted = client.post(f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft", headers=h)
    assert drafted.status_code == 200

    chat = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft/chat",
        headers=h,
        json={"message": "压缩人天表", "body_md": drafted.json()["body_md"]},
    )
    assert chat.status_code == 200
    body = chat.json()
    assert body["body_md"] == _revised_markdown().strip()
    assert body["messages"][-2] == {"role": "user", "content": "压缩人天表"}
    assert body["messages"][-1] == {"role": "assistant", "content": "已压缩人天表"}
    assert mock_llm.call_args.kwargs.get("json_mode") is True
    prompt = mock_llm.call_args.args[0][-1]["content"]
    assert "压缩人天表" in prompt
    assert "当前测试计划草稿" in prompt

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.status_code == 200
    assert got.json()["plan_draft_md"] == _revised_markdown().strip()
    assert got.json()["plan_draft_messages_json"] == body["messages"]


@patch("app.services.functional.test_plan_draft.chat_complete")
def test_chat_without_draft_returns_400(mock_llm):
    _user("plan_draft_chat_miss", "a")
    tok = _login("plan_draft_chat_miss", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    chat = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft/chat",
        headers=h,
        json={"message": "补风险"},
    )
    assert chat.status_code == 400
    assert chat.json()["message"] == "请先生成测试计划草稿"
    mock_llm.assert_not_called()


@patch("app.services.functional.test_plan_draft.chat_complete")
def test_regenerate_resets_chat_messages(mock_llm):
    mock_llm.side_effect = [
        _markdown(),
        json.dumps(
            {"reply": "已改", "body_md": _revised_markdown()},
            ensure_ascii=False,
        ),
        _markdown(),
    ]
    _user("plan_draft_regen", "a")
    tok = _login("plan_draft_regen", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录需求", "body_md": "登录需要账号和密码", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    client.post(f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft", headers=h)
    client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft/chat",
        headers=h,
        json={"message": "改一下"},
    )
    again = client.post(f"/api/v1/projects/{pid}/requirements/{rid}/test-plan-draft", headers=h)
    assert again.status_code == 200
    assert again.json()["messages"] == [
        {"role": "assistant", "content": "已生成草稿，可直接说明修改意见。"}
    ]
    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.json()["plan_draft_messages_json"] == again.json()["messages"]
