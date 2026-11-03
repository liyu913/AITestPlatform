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
        json={"name": "AnalysisChatProj"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _analysis() -> dict:
    return {
        "features": [
            {
                "module": "账号",
                "items": [{"name": "账号密码登录", "detail": "用户登录"}],
            }
        ],
        "ui": {
            "summary": "登录页",
            "pages": [
                {
                    "name": "登录页",
                    "description": "账号密码输入",
                    "areas": ["账号输入框", "登录按钮"],
                }
            ],
        },
        "unclear": [{"item": "密码规则", "reason": "未说明长度"}],
        "image_count": 0,
    }


def _revised_analysis() -> dict:
    return {
        "features": [
            {
                "module": "账号",
                "items": [
                    {"name": "账号密码登录", "detail": "用户登录"},
                    {"name": "验证码登录", "detail": "短信验证码"},
                ],
            }
        ],
        "ui": {
            "summary": "登录页含验证码入口",
            "pages": [
                {
                    "name": "登录页",
                    "description": "账号密码与验证码",
                    "areas": ["账号输入框", "验证码", "登录按钮"],
                }
            ],
        },
        "unclear": [],
    }


def _set_analysis(requirement_id: int) -> None:
    db = TestingSessionLocal()
    try:
        row = db.get(Requirement, requirement_id)
        assert row is not None
        row.analysis_json = _analysis()
        row.analysis_messages_json = [{"role": "assistant", "content": "已生成分析，可直接说明修改意见。"}]
        row.analysis_pending_json = None
        db.commit()
    finally:
        db.close()


@patch("app.services.functional.requirement_analysis.chat_complete")
def test_chat_writes_pending_without_overwriting_analysis(mock_llm):
    mock_llm.return_value = json.dumps(
        {"reply": "已补充验证码登录", "analysis": _revised_analysis()},
        ensure_ascii=False,
    )
    _user("analysis_chat", "a")
    tok = _login("analysis_chat", "a")
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

    chat = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/chat",
        headers=h,
        json={"message": "补充验证码登录"},
    )
    assert chat.status_code == 200
    body = chat.json()
    assert body["reply"] == "已补充验证码登录"
    assert body["analysis"]["features"][0]["items"][0]["name"] == "账号密码登录"
    assert len(body["pending_analysis"]["features"][0]["items"]) == 2
    assert body["messages"][-2] == {"role": "user", "content": "补充验证码登录"}
    assert body["messages"][-1] == {"role": "assistant", "content": "已补充验证码登录"}
    assert mock_llm.call_args.kwargs.get("json_mode") is True

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.status_code == 200
    row = got.json()
    assert row["analysis_json"]["unclear"][0]["item"] == "密码规则"
    assert row["analysis_pending_json"]["unclear"] == []
    assert len(row["analysis_pending_json"]["features"][0]["items"]) == 2


@patch("app.services.functional.requirement_analysis.chat_complete")
def test_accept_applies_pending(mock_llm):
    mock_llm.return_value = json.dumps(
        {"reply": "已改", "analysis": _revised_analysis()},
        ensure_ascii=False,
    )
    _user("analysis_accept", "a")
    tok = _login("analysis_accept", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/chat",
        headers=h,
        json={"message": "改一下"},
    )

    accepted = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/accept",
        headers=h,
    )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["pending_analysis"] is None
    assert len(body["analysis"]["features"][0]["items"]) == 2
    assert body["analysis"]["unclear"] == []

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.json()["analysis_pending_json"] is None
    assert got.json()["analysis_json"]["unclear"] == []


@patch("app.services.functional.requirement_analysis.chat_complete")
def test_discard_clears_pending_keeps_messages(mock_llm):
    mock_llm.return_value = json.dumps(
        {"reply": "已改", "analysis": _revised_analysis()},
        ensure_ascii=False,
    )
    _user("analysis_discard", "a")
    tok = _login("analysis_discard", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    chat = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/chat",
        headers=h,
        json={"message": "改一下"},
    )
    messages = chat.json()["messages"]

    discarded = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/discard",
        headers=h,
    )
    assert discarded.status_code == 200
    assert discarded.json()["pending_analysis"] is None
    assert discarded.json()["messages"] == messages
    assert discarded.json()["analysis"]["unclear"][0]["item"] == "密码规则"

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.json()["analysis_pending_json"] is None
    assert got.json()["analysis_messages_json"] == messages


def test_chat_without_analysis_returns_400():
    _user("analysis_chat_miss", "a")
    tok = _login("analysis_chat_miss", "a")
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
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/chat",
        headers=h,
        json={"message": "补一点"},
    )
    assert chat.status_code == 400
    assert chat.json()["message"] == "请先生成需求分析"


@patch(
    "app.services.functional.requirement_content.requirement_assets_for_ai",
    return_value=("登录正文", []),
)
@patch("app.services.functional.requirement_analysis.chat_complete")
def test_analyze_resets_messages_and_pending(mock_llm, _mock_assets):
    mock_llm.side_effect = [
        json.dumps(
            {"reply": "已改", "analysis": _revised_analysis()},
            ensure_ascii=False,
        ),
        json.dumps(_analysis(), ensure_ascii=False),
    ]
    _user("analysis_regen", "a")
    tok = _login("analysis_regen", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/chat",
        headers=h,
        json={"message": "改一下"},
    )

    again = client.post(f"/api/v1/projects/{pid}/requirements/{rid}/analyze", headers=h)
    assert again.status_code == 200
    body = again.json()
    assert body["analysis_pending_json"] is None
    assert body["analysis_messages_json"] == [
        {"role": "assistant", "content": "已生成分析，可直接说明修改意见。"}
    ]
    assert body["analysis_json"]["features"][0]["items"][0]["name"] == "账号密码登录"


def test_save_writes_baseline_when_no_pending():
    _user("analysis_save_base", "a")
    tok = _login("analysis_save_base", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)

    edited = _revised_analysis()
    saved = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/save",
        headers=h,
        json={"analysis": edited},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["pending_analysis"] is None
    assert len(body["analysis"]["features"][0]["items"]) == 2
    assert body["analysis"]["unclear"] == []
    assert body["analyzed_at"]

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.json()["analysis_pending_json"] is None
    assert len(got.json()["analysis_json"]["features"][0]["items"]) == 2


@patch("app.services.functional.requirement_analysis.chat_complete")
def test_save_writes_pending_when_pending_exists(mock_llm):
    mock_llm.return_value = json.dumps(
        {"reply": "已改", "analysis": _revised_analysis()},
        ensure_ascii=False,
    )
    _user("analysis_save_pend", "a")
    tok = _login("analysis_save_pend", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    _set_analysis(rid)
    client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/chat",
        headers=h,
        json={"message": "改一下"},
    )

    manual = {
        "features": [
            {
                "module": "账号",
                "items": [{"name": "手改登录", "detail": "手动改的"}],
            }
        ],
        "ui": {"summary": "手改", "pages": []},
        "unclear": [],
    }
    saved = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/save",
        headers=h,
        json={"analysis": manual},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["analysis"]["unclear"][0]["item"] == "密码规则"
    assert body["pending_analysis"]["features"][0]["items"][0]["name"] == "手改登录"

    got = client.get(f"/api/v1/projects/{pid}/requirements/{rid}", headers=h)
    assert got.json()["analysis_json"]["unclear"][0]["item"] == "密码规则"
    assert got.json()["analysis_pending_json"]["features"][0]["items"][0]["name"] == "手改登录"


def test_save_without_analysis_returns_400():
    _user("analysis_save_miss", "a")
    tok = _login("analysis_save_miss", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = _auth(tok)
    created = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录", "body_md": "需要登录", "status": "active"},
    )
    rid = created.json()["id"]
    saved = client.post(
        f"/api/v1/projects/{pid}/requirements/{rid}/analyze/save",
        headers=h,
        json={"analysis": _analysis()},
    )
    assert saved.status_code == 400
    assert saved.json()["message"] == "请先生成需求分析"
