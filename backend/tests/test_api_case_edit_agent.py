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


def _project(token: str, name: str = "CaseEditAgent") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _create_case(client: TestClient, h: dict, pid: int, name: str, method: str, path: str, **extra) -> int:
    payload = {
        "name": name,
        "method": method,
        "path": path,
        "body_type": "json",
        "body": "{}",
        "assertions": [{"type": "status", "expected": 200}],
        "status": "draft",
        **extra,
    }
    r = client.post(f"/api/v1/projects/{pid}/api-cases/", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_case_edit_agent_revise_subset_apply(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_edit_ai", "p")
    tok = _login("case_edit_ai", "p")
    pid = _project(tok, "AICaseEdit")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    c1 = _create_case(client, h, pid, "登录正向", "POST", "/login", headers={"X-A": "1"})
    c2 = _create_case(client, h, pid, "登录异常", "POST", "/login", headers={"X-A": "1"})

    plan1 = {
        "title": "统一加 Authorization",
        "summary": "两处 headers 补 token",
        "revisions": [
            {
                "case_id": c1,
                "name": "登录正向",
                "summary": "补 Authorization",
                "changed_fields": ["headers"],
                "skipped": False,
                "draft": {
                    "headers": {"X-A": "1", "Authorization": "Bearer {{token}}"},
                    "assertions": [{"type": "status", "expected": 200}],
                },
            },
            {
                "case_id": c2,
                "name": "登录异常",
                "summary": "补 Authorization",
                "changed_fields": ["headers"],
                "skipped": False,
                "draft": {
                    "headers": {"X-A": "1", "Authorization": "Bearer {{token}}"},
                    "assertions": [{"type": "status", "expected": 400}],
                },
            },
        ],
        "warnings": [],
    }
    plan2 = {
        "title": "只改正向断言",
        "summary": "正向补 code",
        "revisions": [
            {
                "case_id": c1,
                "name": "登录正向",
                "summary": "补 code 断言",
                "changed_fields": ["headers", "assertions"],
                "draft": {
                    "headers": {"X-A": "1", "Authorization": "Bearer {{token}}"},
                    "assertions": [
                        {"type": "status", "expected": 200},
                        {"type": "jsonpath", "expr": "$.code", "op": "eq", "expected": 0},
                    ],
                },
            },
            {
                "case_id": c2,
                "name": "登录异常",
                "summary": "保持 Authorization",
                "changed_fields": ["headers"],
                "draft": {
                    "headers": {"X-A": "1", "Authorization": "Bearer {{token}}"},
                    "assertions": [{"type": "status", "expected": 400}],
                },
            },
        ],
        "warnings": [],
    }
    mock_llm.side_effect = [
        json.dumps(plan1, ensure_ascii=False),
        json.dumps(plan2, ensure_ascii=False),
    ]

    cr = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions",
        headers=h,
        json={"case_ids": [c1, c2], "prompt": "统一加 Authorization: Bearer {{token}}"},
    )
    assert cr.status_code == 201, cr.text
    sess = cr.json()
    assert sess["status"] == "awaiting_confirm"
    assert sess["case_ids"] == [c1, c2]
    assert len(sess["plan"]["revisions"]) == 2
    assert sess["plan"]["revisions"][0]["draft"]["headers"].get("Authorization")
    assert any(e.get("type") == "interrupted" for e in sess["events"])

    # 尚未写库
    g1 = client.get(f"/api/v1/projects/{pid}/api-cases/{c1}", headers=h).json()
    assert "Authorization" not in (g1.get("headers") or {})

    mr = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions/{sess['id']}/messages",
        headers=h,
        json={"content": "正向再补 $.code=0 断言"},
    )
    assert mr.status_code == 200, mr.text
    assert mr.json()["status"] == "awaiting_confirm"
    assert mr.json()["plan"]["title"] == "只改正向断言"
    assert len(mr.json()["messages"]) >= 2

    conf = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions/{sess['id']}/confirm",
        headers=h,
        json={"action": "confirm", "case_ids": [c1]},
    )
    assert conf.status_code == 200, conf.text
    assert conf.json()["status"] == "confirmed"
    assert conf.json()["applied_case_ids"] == [c1]

    after1 = client.get(f"/api/v1/projects/{pid}/api-cases/{c1}", headers=h).json()
    after2 = client.get(f"/api/v1/projects/{pid}/api-cases/{c2}", headers=h).json()
    assert after1["headers"].get("Authorization") == "Bearer {{token}}"
    assert any(
        a.get("type") == "jsonpath" and a.get("expr") == "$.code" for a in (after1.get("assertions") or [])
    )
    assert "Authorization" not in (after2.get("headers") or {})


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_case_edit_agent_locks_path_by_default(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_edit_lock", "p")
    tok = _login("case_edit_lock", "p")
    pid = _project(tok, "LockPath")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    c1 = _create_case(client, h, pid, "Ping", "GET", "/ping")

    mock_llm.return_value = json.dumps(
        {
            "title": "乱改路径",
            "summary": "模型试图改 path",
            "revisions": [
                {
                    "case_id": c1,
                    "name": "Ping",
                    "summary": "改 path",
                    "changed_fields": ["path", "assertions"],
                    "draft": {
                        "method": "POST",
                        "path": "/hacked",
                        "assertions": [{"type": "status", "expected": 200}],
                    },
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )

    cr = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions",
        headers=h,
        json={"case_ids": [c1], "prompt": "补强断言"},
    )
    assert cr.status_code == 201, cr.text
    rev = cr.json()["plan"]["revisions"][0]
    assert rev["draft"]["path"] == "/ping"
    assert rev["draft"]["method"] == "GET"
    assert "path" not in (rev.get("changed_fields") or [])
    assert "method" not in (rev.get("changed_fields") or [])


def test_case_edit_agent_rejects_over_limit_and_foreign(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_edit_lim", "p")
    tok = _login("case_edit_lim", "p")
    pid = _project(tok, "LimitEdit")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    ids = [_create_case(client, h, pid, f"c{i}", "GET", f"/x/{i}") for i in range(21)]
    over = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions",
        headers=h,
        json={"case_ids": ids, "prompt": "x"},
    )
    assert over.status_code == 422

    foreign = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions",
        headers=h,
        json={"case_ids": [ids[0], 999999], "prompt": "x"},
    )
    assert foreign.status_code == 400


def test_normalize_edit_plan_locks_endpoint():
    from app.services.api_automation.api_case_edit_agent_graph import normalize_edit_plan

    snap = {
        1: {
            "case_id": 1,
            "name": "a",
            "method": "GET",
            "path": "/a",
            "query": {},
            "headers": {},
            "body_type": "none",
            "body": "",
            "assertions": [{"type": "status", "expected": 200}],
            "extractors": [],
            "timeout_ms": 15000,
            "description": "",
            "tags": [],
            "status": "draft",
        }
    }
    plan, errs = normalize_edit_plan(
        {
            "title": "t",
            "revisions": [
                {
                    "case_id": 1,
                    "draft": {"method": "POST", "path": "/b", "headers": {"A": "1"}},
                }
            ],
        },
        snapshots_by_id=snap,
        allow_endpoint_change=False,
    )
    assert errs == []
    assert plan is not None
    assert plan["revisions"][0]["draft"]["method"] == "GET"
    assert plan["revisions"][0]["draft"]["path"] == "/a"
    assert plan["revisions"][0]["draft"]["headers"]["A"] == "1"


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_case_edit_agent_empty_prompt_then_message(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_edit_empty", "p")
    tok = _login("case_edit_empty", "p")
    pid = _project(tok, "EmptyPrompt")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    c1 = _create_case(client, h, pid, "A", "GET", "/a")

    cr = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions",
        headers=h,
        json={"case_ids": [c1], "prompt": ""},
    )
    assert cr.status_code == 201, cr.text
    assert cr.json()["status"] == "awaiting_confirm"
    assert (cr.json().get("plan") or {}).get("revisions") == []
    assert mock_llm.call_count == 0

    mock_llm.return_value = json.dumps(
        {
            "title": "加头",
            "summary": "ok",
            "revisions": [
                {
                    "case_id": c1,
                    "summary": "加头",
                    "changed_fields": ["headers"],
                    "draft": {"headers": {"X": "1"}},
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    mr = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions/{cr.json()['id']}/messages",
        headers=h,
        json={"content": "headers 加 X=1"},
    )
    assert mr.status_code == 200, mr.text
    assert len(mr.json()["plan"]["revisions"]) == 1

    db = TestingSessionLocal()
    try:
        row = db.get(ApiCase, c1)
        assert row is not None
        assert not (row.headers_json or {}).get("X")
    finally:
        db.close()


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_case_edit_confirm_survives_memorysaver_loss(mock_llm, monkeypatch):
    """Confirm must apply from plan_json even after in-memory checkpoint is wiped."""
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("case_edit_reload", "p")
    tok = _login("case_edit_reload", "p")
    pid = _project(tok, "ReloadEdit")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    c1 = _create_case(client, h, pid, "ReloadCase", "POST", "/reload", headers={"Keep": "1"})

    mock_llm.return_value = json.dumps(
        {
            "title": "加鉴权",
            "summary": "headers 补 token",
            "revisions": [
                {
                    "case_id": c1,
                    "name": "ReloadCase",
                    "summary": "补 Authorization",
                    "changed_fields": ["headers", "assertions"],
                    "draft": {
                        "headers": {"Keep": "1", "Authorization": "Bearer {{token}}"},
                        "assertions": [
                            {"type": "status", "expected": 200},
                            {"type": "jsonpath", "expr": "$.code", "op": "eq", "expected": 0},
                        ],
                    },
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )

    cr = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions",
        headers=h,
        json={"case_ids": [c1], "prompt": "统一加 Authorization"},
    )
    assert cr.status_code == 201, cr.text
    assert cr.json()["status"] == "awaiting_confirm"
    sid = cr.json()["id"]
    assert cr.json()["plan"]["revisions"][0]["draft"]["headers"].get("Authorization")

    # Simulate uvicorn --reload: drop compiled graph + MemorySaver checkpoints
    from app.api.api_automation import api_case_edit_agent as edit_mod
    from app.services.api_automation import api_case_edit_agent_graph as graph_mod

    graph_mod.reset_compiled_graph()
    edit_mod.set_db_factory(TestingSessionLocal)

    conf = client.post(
        f"/api/v1/projects/{pid}/api-case-edit-agent/sessions/{sid}/confirm",
        headers=h,
        json={"action": "confirm", "case_ids": [c1]},
    )
    assert conf.status_code == 200, conf.text
    assert conf.json()["status"] == "confirmed"
    assert conf.json()["applied_case_ids"] == [c1]

    after = client.get(f"/api/v1/projects/{pid}/api-cases/{c1}", headers=h).json()
    assert after["headers"].get("Authorization") == "Bearer {{token}}"
    assert after["headers"].get("Keep") == "1"
    assert any(
        a.get("type") == "jsonpath" and a.get("expr") == "$.code" for a in (after.get("assertions") or [])
    )
