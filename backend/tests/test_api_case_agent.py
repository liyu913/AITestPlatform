import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.api_automation.api_automation import ApiCase
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


def _project(token: str, name: str = "CaseAgent") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _create_def(client: TestClient, h: dict, pid: int, name: str, method: str, path: str, **extra) -> int:
    payload = {"name": name, "method": method, "path": path, "status": "active", **extra}
    r = client.post(f"/api/v1/projects/{pid}/api-definitions/", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_case_agent_plan_revise_confirm(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_ai", "p")
    tok = _login("case_ai", "p")
    pid = _project(tok, "AICase")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    d1 = _create_def(
        client,
        h,
        pid,
        "登录",
        "POST",
        "/login",
        body_type="json",
        body_text='{"username":"a","password":"b"}',
    )
    d2 = _create_def(client, h, pid, "查订单", "GET", "/orders/{{orderId}}")

    plan1 = {
        "title": "登录与查订单单接口用例",
        "summary": "各自矩阵，不串联",
        "groups": [
            {
                "definition_id": d1,
                "definition_name": "登录",
                "method": "POST",
                "path": "/login",
                "cases": [
                    {
                        "name": "登录 - 正向-正常请求",
                        "tags": ["auto-gen", "positive"],
                        "method": "POST",
                        "path": "/login",
                        "body_type": "json",
                        "body": '{"username":"a","password":"b"}',
                        "assertions": [{"type": "status", "expected": 200}],
                        "extractors": [
                            {"name": "token", "type": "jsonpath", "expr": "$.data.token"}
                        ],
                        "env_vars_needed": [],
                    },
                    {
                        "name": "登录 - 异常-空Body",
                        "tags": ["auto-gen", "negative"],
                        "method": "POST",
                        "path": "/login",
                        "body_type": "json",
                        "body": "",
                        "assertions": [{"type": "status", "expected": 400}],
                        "extractors": [],
                    },
                ],
            },
            {
                "definition_id": d2,
                "definition_name": "查订单",
                "method": "GET",
                "path": "/orders/{{orderId}}",
                "cases": [
                    {
                        "name": "查订单 - 正向-正常请求",
                        "tags": ["auto-gen", "positive"],
                        "method": "GET",
                        "path": "/orders/{{orderId}}",
                        "assertions": [{"type": "status", "expected": 200}],
                        "env_vars_needed": ["orderId"],
                    }
                ],
            },
        ],
        "warnings": ["查订单依赖 orderId，请配置环境或改用场景 Agent"],
        "needs_user_input": [],
    }
    plan2 = {
        "title": "精简后的登录用例",
        "summary": "去掉空 Body",
        "groups": [
            {
                "definition_id": d1,
                "definition_name": "登录",
                "method": "POST",
                "path": "/login",
                "cases": [
                    {
                        "name": "登录 - 正向-正常请求",
                        "tags": ["auto-gen", "positive"],
                        "method": "POST",
                        "path": "/login",
                        "body_type": "json",
                        "body": '{"username":"a","password":"b"}',
                        "assertions": [{"type": "status", "expected": 200}],
                        "extractors": [
                            {"name": "token", "type": "jsonpath", "expr": "$.data.token"}
                        ],
                    }
                ],
            },
            {
                "definition_id": d2,
                "definition_name": "查订单",
                "method": "GET",
                "path": "/orders/{{orderId}}",
                "cases": [
                    {
                        "name": "查订单 - 正向-正常请求",
                        "tags": ["auto-gen", "positive"],
                        "method": "GET",
                        "path": "/orders/{{orderId}}",
                        "assertions": [{"type": "status", "expected": 200}],
                        "env_vars_needed": ["orderId"],
                    }
                ],
            },
        ],
        "warnings": [],
    }
    mock_llm.side_effect = [
        json.dumps(plan1, ensure_ascii=False),
        json.dumps(plan2, ensure_ascii=False),
    ]

    before = client.get(f"/api/v1/projects/{pid}/api-cases/?page=1&page_size=100", headers=h)
    n_before = before.json()["total"]

    cr = client.post(
        f"/api/v1/projects/{pid}/api-case-agent/sessions",
        headers=h,
        json={"definition_ids": [d1, d2]},
    )
    assert cr.status_code == 201, cr.text
    sess = cr.json()
    assert sess["status"] == "awaiting_confirm"
    assert sess["plan"]["title"] == "登录与查订单单接口用例"
    assert sess["candidate_definition_ids"] == [d1, d2]
    assert len(sess["plan"]["groups"]) == 2
    assert any("orderId" in (w or "") for w in (sess["plan"].get("warnings") or []))
    login_group = next(g for g in sess["plan"]["groups"] if g["definition_id"] == d1)
    assert len(login_group["cases"]) == 2
    assert login_group["cases"][0]["extractors"][0]["name"] == "token"
    assert any(e.get("type") == "interrupted" for e in sess["events"])
    assert client.get(f"/api/v1/projects/{pid}/api-cases/?page=1&page_size=100", headers=h).json()["total"] == n_before

    mr = client.post(
        f"/api/v1/projects/{pid}/api-case-agent/sessions/{sess['id']}/messages",
        headers=h,
        json={"content": "去掉登录的空 Body 用例"},
    )
    assert mr.status_code == 200, mr.text
    assert mr.json()["status"] == "awaiting_confirm"
    assert mr.json()["plan"]["title"] == "精简后的登录用例"
    login_after = next(g for g in mr.json()["plan"]["groups"] if g["definition_id"] == d1)
    assert len(login_after["cases"]) == 1
    assert len(mr.json()["messages"]) >= 2

    assert client.get(f"/api/v1/projects/{pid}/api-cases/?page=1&page_size=100", headers=h).json()["total"] == n_before

    conf = client.post(
        f"/api/v1/projects/{pid}/api-case-agent/sessions/{sess['id']}/confirm",
        headers=h,
        json={"action": "confirm"},
    )
    assert conf.status_code == 200, conf.text
    assert conf.json()["status"] == "confirmed"
    created_ids = conf.json()["created_case_ids"]
    assert len(created_ids) == 2

    after = client.get(f"/api/v1/projects/{pid}/api-cases/?page=1&page_size=100", headers=h)
    assert after.json()["total"] == n_before + 2

    db = TestingSessionLocal()
    try:
        cases = [db.get(ApiCase, cid) for cid in created_ids]
        assert all(c is not None for c in cases)
        by_api = {c.api_id: c for c in cases}
        assert d1 in by_api and d2 in by_api
        assert by_api[d1].extractors_json and by_api[d1].extractors_json[0]["name"] == "token"
        assert "{{orderId}}" in (by_api[d2].path or "")
        assert "ai-case-agent" in (by_api[d1].tags_json or [])
    finally:
        db.close()


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_case_agent_cancel_no_cases(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_cancel", "p")
    tok = _login("case_cancel", "p")
    pid = _project(tok, "CancelCase")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    d1 = _create_def(client, h, pid, "Ping", "GET", "/ping")
    mock_llm.return_value = json.dumps(
        {
            "title": "t",
            "summary": "s",
            "groups": [
                {
                    "definition_id": d1,
                    "definition_name": "Ping",
                    "method": "GET",
                    "path": "/ping",
                    "cases": [
                        {
                            "name": "Ping - 正向",
                            "tags": ["positive"],
                            "assertions": [{"type": "status", "expected": 200}],
                        }
                    ],
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    before = client.get(f"/api/v1/projects/{pid}/api-cases/?page=1&page_size=100", headers=h).json()["total"]
    cr = client.post(
        f"/api/v1/projects/{pid}/api-case-agent/sessions",
        headers=h,
        json={"definition_ids": [d1]},
    )
    assert cr.status_code == 201
    sid = cr.json()["id"]
    conf = client.post(
        f"/api/v1/projects/{pid}/api-case-agent/sessions/{sid}/confirm",
        headers=h,
        json={"action": "cancel"},
    )
    assert conf.status_code == 200
    assert conf.json()["status"] == "cancelled"
    assert conf.json()["created_case_ids"] == []
    assert (
        client.get(f"/api/v1/projects/{pid}/api-cases/?page=1&page_size=100", headers=h).json()["total"]
        == before
    )


def test_normalize_case_plan_rejects_foreign_definition():
    from app.services.api_automation.api_case_agent_graph import normalize_case_plan

    plan, errs = normalize_case_plan(
        {
            "title": "x",
            "groups": [
                {
                    "definition_id": 99,
                    "cases": [{"name": "a", "assertions": [{"type": "status", "expected": 200}]}],
                }
            ],
        },
        allowed_definition_ids={1},
        catalog_by_id={1: {"id": 1, "name": "a", "method": "GET", "path": "/"}},
    )
    assert plan is None
    assert errs
