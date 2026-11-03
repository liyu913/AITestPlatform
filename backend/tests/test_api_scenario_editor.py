"""Scenario editor agent: revise current steps without persisting."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
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


def _project(token: str, name: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth_client(username: str, project_name: str):
    _user(username, "p")
    token = _login(username, "p")
    pid = _project(token, project_name)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, pid


def _step(sid: str, method: str, path: str, **extra) -> dict:
    step = {
        "id": sid,
        "name": extra.pop("name", sid),
        "method": method,
        "path": path,
        "query": extra.pop("query", {}),
        "headers": extra.pop("headers", {}),
        "body_type": extra.pop("body_type", "none"),
        "body": extra.pop("body", ""),
        "assertions": extra.pop("assertions", [{"type": "status", "expected": 200}]),
        "extractors": extra.pop("extractors", []),
        "on_pass": extra.pop("on_pass", "end"),
        "on_fail": extra.pop("on_fail", "end"),
    }
    step.update(extra)
    return step


def _create_suite(client: TestClient, headers: dict, pid: int, steps: list[dict]) -> int:
    response = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=headers,
        json={"name": "editor", "steps": steps},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _revise(client: TestClient, headers: dict, pid: int, suite_id: int, message: str, steps: list[dict]):
    return client.post(
        f"/api/v1/projects/{pid}/api-suites/{suite_id}/agent/revise",
        headers=headers,
        json={"message": message, "steps": steps, "messages": []},
    )


@patch("app.services.api_automation.api_scenario_editor.llm_json")
def test_editor_updates_header_and_keeps_unmentioned_body(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    client, headers, pid = _auth_client("editor_keep", "EditorKeep")
    steps = [
        _step(
            "s1",
            "POST",
            "/login",
            name="登录",
            body_type="json",
            body='{"user":"a"}',
            headers={"X-Keep": "1"},
            assertions=[{"type": "status", "expected": 201}],
            extractors=[{"name": "token", "type": "jsonpath", "expr": "$.data.token"}],
            on_pass="s2",
        ),
        _step(
            "s2",
            "GET",
            "/me",
            name="查我",
            body="leave-me",
            headers={"X-Old": "0"},
            on_pass="end",
        ),
    ]
    suite_id = _create_suite(client, headers, pid, steps)
    mock_llm.return_value = {
        "summary": "第二步改为携带 token",
        "warnings": ["需确保上一步提取了 token"],
        "steps": [
            {"id": "s1", "method": "POST", "path": "/login"},
            {
                "id": "s2",
                "method": "GET",
                "path": "/me",
                "headers": {"Authorization": "Bearer {{token}}"},
                "assertions": [
                    {"type": "status", "expected": 200},
                    {"type": "jsonpath", "expr": "$.id", "op": "exists"},
                ],
            },
        ],
    }

    response = _revise(client, headers, pid, suite_id, "给第二步加上 Authorization", steps)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == "第二步改为携带 token"
    assert body["warnings"] == ["需确保上一步提取了 token"]
    revised = body["steps"]
    assert revised[0]["body"] == '{"user":"a"}'
    assert revised[0]["headers"]["X-Keep"] == "1"
    assert revised[0]["assertions"][0]["expected"] == 201
    assert revised[0]["extractors"][0]["name"] == "token"
    assert revised[0]["on_pass"] == "s2"
    assert revised[1]["headers"]["Authorization"] == "Bearer {{token}}"
    assert revised[1]["body"] == "leave-me"
    assert any(item.get("expr") == "$.id" for item in revised[1]["assertions"])

    stored = client.get(f"/api/v1/projects/{pid}/api-suites/{suite_id}", headers=headers)
    assert stored.status_code == 200
    assert stored.json()["steps"][0]["body"] == '{"user":"a"}'
    assert stored.json()["steps"][1]["headers"].get("Authorization") is None


@patch("app.services.api_automation.api_scenario_editor.llm_json")
def test_editor_adds_step_from_definition(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    client, headers, pid = _auth_client("editor_add", "EditorAdd")
    created = client.post(
        f"/api/v1/projects/{pid}/api-definitions/",
        headers=headers,
        json={
            "name": "查订单",
            "method": "GET",
            "path": "/orders",
            "headers": {"X-From-Def": "1"},
            "body_type": "json",
            "body": '{"page":1}',
        },
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]
    steps = [_step("s1", "POST", "/login", name="登录", body="keep", on_pass="end")]
    suite_id = _create_suite(client, headers, pid, steps)
    mock_llm.return_value = {
        "summary": "追加查询订单",
        "warnings": [],
        "steps": [
            {"id": "s1", "on_pass": "s2"},
            {
                "id": "s2",
                "definition_id": definition_id,
                "consume_hints": [
                    {
                        "var": "token",
                        "from_step": "s1",
                        "in": "header",
                        "key": "Authorization",
                        "template": "Bearer {{token}}",
                    }
                ],
            },
        ],
    }

    response = _revise(client, headers, pid, suite_id, "再接一个查询订单", steps)
    assert response.status_code == 200, response.text
    revised = response.json()["steps"]
    assert len(revised) == 2
    assert revised[0]["body"] == "keep"
    assert revised[0]["on_pass"] == "s2"
    assert revised[1]["definition_id"] == definition_id
    assert revised[1]["method"] == "GET"
    assert revised[1]["path"] == "/orders"
    assert revised[1]["body"] == '{"page":1}'
    assert revised[1]["headers"]["X-From-Def"] == "1"
    assert revised[1]["headers"]["Authorization"] == "Bearer {{token}}"
    assert revised[1]["name"] == "查订单"


@patch("app.services.api_automation.api_scenario_editor.llm_json")
def test_editor_rejects_broken_jump_unknown_definition_and_bad_when(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    client, headers, pid = _auth_client("editor_bad", "EditorBad")
    steps = [_step("s1", "GET", "/health", name="健康", body="same", on_pass="end")]
    suite_id = _create_suite(client, headers, pid, steps)

    mock_llm.return_value = {
        "summary": "坏跳转",
        "steps": [{"id": "s1", "on_pass": "s9"}],
    }
    broken = _revise(client, headers, pid, suite_id, "跳到不存在的步骤", steps)
    assert broken.status_code == 400
    assert "不存在" in broken.json()["message"]

    mock_llm.return_value = {
        "summary": "编造接口",
        "steps": [{"id": "s1"}, {"id": "s2", "definition_id": 999999, "on_pass": "end"}],
    }
    fake = _revise(client, headers, pid, suite_id, "加一个不存在的接口", steps)
    assert fake.status_code == 400
    assert "不存在的接口" in fake.json()["message"]

    mock_llm.return_value = {
        "summary": "非法条件",
        "steps": [{"id": "s1", "when": {"op": "gt", "left": "{{code}}", "right": "0"}}],
    }
    bad_when = _revise(client, headers, pid, suite_id, "加上大于条件", steps)
    assert bad_when.status_code == 400
    assert "when" in bad_when.json()["message"]

    stored = client.get(f"/api/v1/projects/{pid}/api-suites/{suite_id}", headers=headers)
    assert stored.json()["steps"][0]["body"] == "same"
    assert stored.json()["steps"][0]["on_pass"] == "end"
