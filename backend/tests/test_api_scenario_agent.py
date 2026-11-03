import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.api_automation.api_automation import ApiCase, ApiSuite
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


def _project(token: str, name: str = "Scen") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _create_def(client: TestClient, h: dict, pid: int, name: str, method: str, path: str) -> int:
    r = client.post(
        f"/api/v1/projects/{pid}/api-definitions/",
        headers=h,
        json={"name": name, "method": method, "path": path, "status": "active"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_suite_steps_branch_on_fail(monkeypatch):
    import httpx
    from unittest.mock import patch as _patch

    monkeypatch.setattr(settings, "settings_encryption_key", "")
    _user("scen_branch", "p")
    tok = _login("scen_branch", "p")
    pid = _project(tok, "Branch")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201

    c1 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "login",
            "method": "POST",
            "path": "/login",
            "assertions": [{"type": "status", "expected": 200}],
        },
    )
    c2 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "order",
            "method": "POST",
            "path": "/order",
            "assertions": [{"type": "status", "expected": 200}],
        },
    )
    assert c1.status_code == 201 and c2.status_code == 201
    id1, id2 = c1.json()["id"], c2.json()["id"]

    sr = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={
            "name": "branch",
            "steps": [
                {"id": "s1", "case_id": id1, "on_pass": "s2", "on_fail": "end"},
                {"id": "s2", "case_id": id2, "on_pass": "end", "on_fail": "end"},
            ],
        },
    )
    assert sr.status_code == 201, sr.text
    suite_id = sr.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(401, json={"err": "bad"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with _patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-suites/{suite_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    assert rr.json()["status"] == "failed"
    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{rr.json()['id']}/steps", headers=h)
    assert len(steps.json()) == 1
    assert steps.json()[0]["step_key"] == "s1"


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_scenario_agent_defs_plan_revise_confirm(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("scen_ai", "p")
    tok = _login("scen_ai", "p")
    pid = _project(tok, "AIScen")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    d1 = _create_def(client, h, pid, "登录", "POST", "/login")
    d2 = _create_def(client, h, pid, "查我", "GET", "/me")

    plan1 = {
        "title": "登录后查我",
        "summary": "先登录再查资料",
        "steps": [
            {
                "id": "s1",
                "definition_id": d1,
                "definition_name": "登录",
                "method": "POST",
                "path": "/login",
                "on_pass": "s2",
                "on_fail": "end",
                "when": None,
                "extract_hints": ["token"],
                "consume_hints": [],
                "rationale": "产出鉴权 token",
            },
            {
                "id": "s2",
                "definition_id": d2,
                "definition_name": "查我",
                "method": "GET",
                "path": "/me",
                "on_pass": "end",
                "on_fail": "end",
                "when": None,
                "extract_hints": [],
                "consume_hints": [
                    {
                        "var": "token",
                        "from_step": "s1",
                        "in": "header",
                        "key": "Authorization",
                        "template": "Bearer {{token}}",
                    }
                ],
                "rationale": "携带 token 查询",
            },
        ],
        "warnings": [],
    }
    plan2 = {
        "title": "查我后登录修正",
        "summary": "按用户要求调换顺序",
        "steps": [
            {
                "id": "s1",
                "definition_id": d2,
                "definition_name": "查我",
                "on_pass": "s2",
                "on_fail": "end",
            },
            {
                "id": "s2",
                "definition_id": d1,
                "definition_name": "登录",
                "on_pass": "end",
                "on_fail": "end",
            },
        ],
        "warnings": [],
    }
    mock_llm.side_effect = [
        json.dumps(plan1, ensure_ascii=False),
        json.dumps(plan2, ensure_ascii=False),
    ]

    before = client.get(f"/api/v1/projects/{pid}/api-suites/?page=1&page_size=100", headers=h)
    n_before = before.json()["total"]

    # prompt optional
    cr = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions",
        headers=h,
        json={"definition_ids": [d1, d2]},
    )
    assert cr.status_code == 201, cr.text
    sess = cr.json()
    assert sess["status"] == "awaiting_confirm"
    assert sess["plan"]["title"] == "登录后查我"
    assert sess["candidate_definition_ids"] == [d1, d2]
    assert sess["plan"]["steps"][0]["extract_hints"] == ["token"]
    assert sess["plan"]["steps"][1]["consume_hints"][0]["var"] == "token"
    assert any(
        a.get("type") == "jsonpath" and a.get("expr") == "$.token" and a.get("op") == "exists"
        for a in (sess["plan"]["steps"][0].get("assertions") or [])
    )
    assert any(e.get("type") == "interrupted" for e in sess["events"])
    assert client.get(f"/api/v1/projects/{pid}/api-suites/?page=1&page_size=100", headers=h).json()["total"] == n_before

    # multi-turn revise
    mr = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{sess['id']}/messages",
        headers=h,
        json={"content": "把查我放到登录前面"},
    )
    assert mr.status_code == 200, mr.text
    assert mr.json()["status"] == "awaiting_confirm"
    assert mr.json()["plan"]["title"] == "查我后登录修正"
    assert mr.json()["plan"]["steps"][0]["definition_id"] == d2
    assert len(mr.json()["messages"]) >= 2

    # still no suite
    assert client.get(f"/api/v1/projects/{pid}/api-suites/?page=1&page_size=100", headers=h).json()["total"] == n_before

    conf = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{sess['id']}/confirm",
        headers=h,
        json={"action": "confirm"},
    )
    assert conf.status_code == 200, conf.text
    assert conf.json()["status"] == "confirmed"
    suite_id = conf.json()["suite_id"]
    assert suite_id is not None

    after = client.get(f"/api/v1/projects/{pid}/api-suites/?page=1&page_size=100", headers=h)
    assert after.json()["total"] == n_before + 1

    db = TestingSessionLocal()
    try:
        suite = db.get(ApiSuite, suite_id)
        assert suite is not None
        assert isinstance(suite.steps_json, list) and len(suite.steps_json) == 2
        # steps own inline request; no case binding required
        by_def = {int(st["definition_id"]): st for st in suite.steps_json}
        for st in suite.steps_json:
            assert st.get("method")
            assert st.get("path")
            assert st.get("case_id") is None
            asserts = st.get("assertions") or []
            assert any(a.get("type") == "status" for a in asserts)
        # revise swapped order — materialize uses latest plan (plan2) without hints;
        # still must have executable inline steps
        assert len(by_def) == 2
    finally:
        db.close()


def test_apply_consume_hints_and_normalize_definition_steps():
    from app.services.api_automation.api_suite_steps import (
        apply_consume_hints_to_payload,
        normalize_definition_steps,
    )

    payload = {
        "method": "GET",
        "path": "/me",
        "query": {},
        "headers": {},
        "body_type": "none",
        "body": "",
        "assertions": [],
        "extractors": [],
        "timeout_ms": 15000,
    }
    out = apply_consume_hints_to_payload(
        payload,
        [
            {
                "var": "token",
                "from_step": "s1",
                "in": "header",
                "key": "Authorization",
                "template": "Bearer {{token}}",
            },
            {"var": "uid", "in": "query", "key": "userId"},
        ],
    )
    assert out["headers"]["Authorization"] == "Bearer {{token}}"
    assert out["query"]["userId"] == "{{uid}}"

    # Path consume: already-doubled braces must not become {{{id}}}
    path_out = apply_consume_hints_to_payload(
        {**payload, "path": "/items/{{id}}"},
        [{"var": "itemId", "in": "path", "key": "id"}],
    )
    assert path_out["path"] == "/items/{{itemId}}"
    legacy_path = apply_consume_hints_to_payload(
        {**payload, "path": "/items/{id}"},
        [{"var": "itemId", "in": "path", "key": "id"}],
    )
    assert legacy_path["path"] == "/items/{{itemId}}"

    steps = normalize_definition_steps(
        [
            {
                "id": "s1",
                "definition_id": 1,
                "extract_hints": ["token", ""],
                "consume_hints": [{"var": "token", "in": "header", "key": "Authorization"}],
                "assertions": [{"type": "jsonpath", "expr": "$.code", "op": "eq", "expected": 0}],
            }
        ]
    )
    assert steps[0]["extract_hints"] == ["token"]
    assert steps[0]["consume_hints"][0]["template"] == "{{token}}"
    assert steps[0]["assertions"][0]["expr"] == "$.code"


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_scenario_agent_cancel_no_suite(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("scen_cancel", "p")
    tok = _login("scen_cancel", "p")
    pid = _project(tok, "Cancel")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    did = _create_def(client, h, pid, "a", "GET", "/a")
    mock_llm.return_value = json.dumps(
        {
            "title": "t",
            "summary": "s",
            "steps": [{"id": "s1", "definition_id": did, "on_pass": "end", "on_fail": "end"}],
            "warnings": [],
        }
    )
    cr = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions",
        headers=h,
        json={"prompt": "可选描述", "definition_ids": [did]},
    )
    assert cr.status_code == 201
    conf = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{cr.json()['id']}/confirm",
        headers=h,
        json={"action": "cancel"},
    )
    assert conf.status_code == 200
    assert conf.json()["status"] == "cancelled"
    assert conf.json()["suite_id"] is None


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_triage_plan_then_cancel(mock_llm, monkeypatch):
    import httpx
    from unittest.mock import patch as _patch

    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", "")
    _user("scen_triage", "p")
    tok = _login("scen_triage", "p")
    pid = _project(tok, "Triage")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    c1 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "bad",
            "method": "GET",
            "path": "/bad",
            "assertions": [{"type": "status", "expected": 200}],
        },
    )
    case_id = c1.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"err": 1})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with _patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-cases/{case_id}/run",
            headers=h,
            json={"sync": True},
        )
    run_id = rr.json()["id"]
    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{run_id}/steps", headers=h).json()
    step_id = steps[0]["id"]

    mock_llm.return_value = json.dumps(
        {
            "summary": "服务端 500",
            "likely_causes": ["下游故障"],
            "suggested_patches": [],
            "warnings": [],
        }
    )
    tr = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/steps/{step_id}/ai-triage",
        headers=h,
        json={},
    )
    assert tr.status_code == 201, tr.text
    tid = tr.json()["id"]
    conf = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/ai-triage/{tid}/confirm",
        headers=h,
        json={"action": "cancel"},
    )
    assert conf.status_code == 200
    assert conf.json()["status"] == "cancelled"


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_triage_confirm_writes_suite_step_path_patch(mock_llm, monkeypatch):
    """Scenario steps keep assertions on the suite, and the model often returns a path map."""
    import httpx
    from unittest.mock import patch as _patch

    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", "")
    _user("scen_triage_apply", "p")
    tok = _login("scen_triage_apply", "p")
    pid = _project(tok, "TriageApply")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    suite = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={
            "name": "创建链路",
            "steps": [
                {
                    "id": "s1",
                    "name": "创建用例",
                    "method": "POST",
                    "path": "/cases",
                    "assertions": [{"type": "jsonpath", "expr": "$.data.case_id", "op": "exists"}],
                    "extractors": [{"name": "case_id", "type": "jsonpath", "expr": "$.data.case_id"}],
                }
            ],
        },
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 56})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with _patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(f"/api/v1/projects/{pid}/api-suites/{suite_id}/run", headers=h, json={"sync": True})
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]
    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{run_id}/steps", headers=h).json()
    assert steps[0]["status"] in ("failed", "error")
    assert steps[0]["case_id"] is None

    mock_llm.return_value = json.dumps(
        {
            "summary": "响应是根级 id",
            "likely_causes": ["没有 data 包装"],
            "suggested_patches": [
                {"target": "assertions", "patch": {"$.data.case_id": "$.id"}, "rationale": "改断言路径"},
                {"target": "extractors", "patch": {"case_id": "$.id"}, "rationale": "改提取路径"},
            ],
            "warnings": [],
        }
    )
    tr = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/steps/{steps[0]['id']}/ai-triage",
        headers=h,
        json={},
    )
    assert tr.status_code == 201, tr.text
    tid = tr.json()["id"]
    skipped = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/ai-triage/{tid}/confirm",
        headers=h,
        json={"action": "confirm", "apply_patch": False},
    )
    assert skipped.status_code == 200, skipped.text
    assert "未修改" in skipped.json()["patch_message"]
    still = client.get(f"/api/v1/projects/{pid}/api-suites/{suite_id}", headers=h).json()
    assert still["steps"][0]["assertions"][0]["expr"] == "$.data.case_id"

    tr2 = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/steps/{steps[0]['id']}/ai-triage",
        headers=h,
        json={},
    )
    assert tr2.status_code == 201, tr2.text
    applied = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/ai-triage/{tr2.json()['id']}/confirm",
        headers=h,
        json={"action": "confirm", "apply_patch": True},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["patch_message"].startswith("已写入"), applied.text
    saved = client.get(f"/api/v1/projects/{pid}/api-suites/{suite_id}", headers=h).json()
    assert saved["steps"][0]["assertions"][0]["expr"] == "$.id"
    assert saved["steps"][0]["extractors"][0]["expr"] == "$.id"


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_triage_confirm_writes_case_path_patch(mock_llm, monkeypatch):
    import httpx
    from unittest.mock import patch as _patch

    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", "")
    _user("scen_triage_case", "p")
    tok = _login("scen_triage_case", "p")
    pid = _project(tok, "TriageCase")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    created = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "创建用例",
            "method": "POST",
            "path": "/cases",
            "assertions": [{"type": "jsonpath", "expr": "$.data.case_id", "op": "exists"}],
            "extractors": [{"name": "case_id", "from": "jsonpath", "expr": "$.data.case_id"}],
        },
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 56})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with _patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-cases/{case_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]
    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{run_id}/steps", headers=h).json()
    mock_llm.return_value = json.dumps(
        {
            "summary": "根级 id",
            "likely_causes": [],
            "suggested_patches": [
                {"target": "assertions", "patch": {"$.data.case_id": "$.id"}, "rationale": "断言"},
                {"target": "extractors", "patch": {"case_id": "$.id"}, "rationale": "提取"},
            ],
            "warnings": [],
        }
    )
    tr = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/steps/{steps[0]['id']}/ai-triage",
        headers=h,
        json={},
    )
    assert tr.status_code == 201, tr.text
    applied = client.post(
        f"/api/v1/projects/{pid}/api-runs/{run_id}/ai-triage/{tr.json()['id']}/confirm",
        headers=h,
        json={"action": "confirm", "apply_patch": True},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["patch_message"].startswith("已写入"), applied.text
    case = client.get(f"/api/v1/projects/{pid}/api-cases/{case_id}", headers=h).json()
    assert case["assertions"][0]["expr"] == "$.id"
    assert case["extractors"][0]["expr"] == "$.id"


def test_build_scenario_assertions_rules():
    from app.services.api_automation.api_assertions import build_scenario_assertions

    rich = {
        "responses": [
            {
                "status": "201",
                "sample": {"code": 0, "success": True, "data": {"id": 1}, "message": "ok"},
                "fields": [
                    {"name": "data.id", "required": True},
                    {"name": "name", "required": False},
                ],
            }
        ]
    }
    out = build_scenario_assertions(rich, extract_hints=["token"])
    by_expr = {(a.get("type"), a.get("expr"), a.get("op")): a for a in out}
    assert by_expr[("status", None, None)]["expected"] == 201
    assert by_expr[("jsonpath", "$.code", "eq")]["expected"] == 0
    assert by_expr[("jsonpath", "$.success", "eq")]["expected"] is True
    assert ("jsonpath", "$.data", "exists") in by_expr
    assert ("jsonpath", "$.message", "exists") in by_expr
    assert ("jsonpath", "$.data.id", "exists") in by_expr
    assert ("jsonpath", "$.data.token", "exists") in by_expr

    random_code = {"responses": [{"status": "200", "sample": {"code": 3847}, "fields": []}]}
    out2 = build_scenario_assertions(random_code)
    assert out2[0] == {"type": "status", "expected": 200}
    assert not any(a.get("expr") == "$.code" for a in out2)

    merged = build_scenario_assertions(
        random_code,
        llm_assertions=[
            {"type": "jsonpath", "expr": "$.msg", "op": "exists"},
            {"type": "nope", "expr": "x"},
            {"type": "status", "expected": 201},
        ],
    )
    assert any(a.get("type") == "status" and a.get("expected") == 201 for a in merged)
    assert any(a.get("expr") == "$.msg" and a.get("op") == "exists" for a in merged)
    assert not any(a.get("type") == "nope" for a in merged)


def test_resolve_extract_expr_flat_vs_envelope():
    from app.services.api_automation.api_assertions import (
        build_scenario_assertions,
        resolve_extract_expr,
        sample_from_catalog_summary,
    )

    flat = {
        "responses": [
            {
                "status": "201",
                "sample": {"id": 53, "name": "demo"},
                "fields": [{"name": "id", "required": True}],
            }
        ]
    }
    assert resolve_extract_expr(flat, "id") == "$.id"
    assert resolve_extract_expr(flat, "caseId") == "$.id"
    assert resolve_extract_expr(flat, "case_id") == "$.id"
    flat_out = build_scenario_assertions(
        flat,
        extract_hints=["caseId"],
        llm_assertions=[{"type": "jsonpath", "expr": "$.data.caseId", "op": "exists"}],
    )
    exprs = [a.get("expr") for a in flat_out if a.get("type") == "jsonpath"]
    assert "$.id" in exprs
    assert "$.data.caseId" not in exprs
    assert not any(isinstance(e, str) and e.startswith("$.data.") for e in exprs)

    enveloped = {
        "responses": [
            {
                "status": "200",
                "sample": {"code": 0, "data": {"token": "t1"}},
                "fields": [{"name": "data.token", "required": True}],
            }
        ]
    }
    assert resolve_extract_expr(enveloped, "token") == "$.data.token"

    # Catalog preview for flat bodies must keep top-level keys so resolve works
    preview = sample_from_catalog_summary(
        {
            "success_status": 201,
            "required_fields": ["id"],
            "envelope_keys": [],
            "envelope_eq": {},
            "response_keys": ["id", "name"],
        }
    )
    assert resolve_extract_expr(preview, "caseId") == "$.id"


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_scenario_agent_assertions_from_spec_and_llm(mock_llm, monkeypatch):
    from app.models.api_automation.api_automation import ApiDefinition, ApiSpec, ApiSpecEndpoint

    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("scen_assert", "p")
    tok = _login("scen_assert", "p")
    pid = _project(tok, "AssertScen")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    did = _create_def(client, h, pid, "创建订单", "POST", "/orders")

    db = TestingSessionLocal()
    try:
        spec = ApiSpec(
            project_id=pid,
            name="orders",
            source_type="openapi",
            raw_text="{}",
            summary_json={"title": "o"},
        )
        db.add(spec)
        db.flush()
        ep = ApiSpecEndpoint(
            spec_id=spec.id,
            project_id=pid,
            method="POST",
            path="/orders",
            summary="创建订单",
            tags_json=[],
            request_sample_json={
                "body_type": "json",
                "body": {},
                "responses": [
                    {
                        "status": "201",
                        "sample": {"code": 0, "success": True, "data": {}},
                        "fields": [{"name": "data.id", "required": True}],
                    }
                ],
            },
        )
        db.add(ep)
        db.flush()
        row = db.get(ApiDefinition, did)
        assert row is not None
        row.spec_id = spec.id
        row.spec_endpoint_id = ep.id
        db.commit()
    finally:
        db.close()

    mock_llm.return_value = json.dumps(
        {
            "title": "下单",
            "summary": "创建订单",
            "steps": [
                {
                    "id": "s1",
                    "definition_id": did,
                    "on_pass": "end",
                    "on_fail": "end",
                    "extract_hints": ["id"],
                    "assertions": [{"type": "contains", "expected": "ok"}],
                }
            ],
            "warnings": [],
        }
    )
    cr = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions",
        headers=h,
        json={"definition_ids": [did]},
    )
    assert cr.status_code == 201, cr.text
    plan_asserts = cr.json()["plan"]["steps"][0]["assertions"]
    assert any(a.get("type") == "status" and a.get("expected") == 201 for a in plan_asserts)
    assert any(a.get("expr") == "$.code" and a.get("expected") == 0 for a in plan_asserts)
    assert any(a.get("expr") == "$.data.id" and a.get("op") == "exists" for a in plan_asserts)
    assert any(a.get("type") == "contains" and a.get("expected") == "ok" for a in plan_asserts)

    conf = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{cr.json()['id']}/confirm",
        headers=h,
        json={"action": "confirm"},
    )
    assert conf.status_code == 200, conf.text
    suite_id = conf.json()["suite_id"]
    db = TestingSessionLocal()
    try:
        suite = db.get(ApiSuite, suite_id)
        assert suite is not None
        saved = suite.steps_json[0]["assertions"]
        assert any(a.get("type") == "status" and a.get("expected") == 201 for a in saved)
        assert any(a.get("expr") == "$.data.id" and a.get("op") == "exists" for a in saved)
        assert any(a.get("type") == "contains" and a.get("expected") == "ok" for a in saved)
        extractors = suite.steps_json[0].get("extractors") or []
        assert any(ex.get("name") == "id" and ex.get("expr") == "$.data.id" for ex in extractors)
    finally:
        db.close()


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_scenario_agent_flat_response_extract_paths(mock_llm, monkeypatch):
    """Flat create responses must not invent $.data.caseId extractors/assertions."""
    from app.models.api_automation.api_automation import ApiDefinition, ApiSpec, ApiSpecEndpoint

    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("scen_flat", "p")
    tok = _login("scen_flat", "p")
    pid = _project(tok, "FlatScen")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    did = _create_def(client, h, pid, "创建用例", "POST", "/cases")

    db = TestingSessionLocal()
    try:
        spec = ApiSpec(
            project_id=pid,
            name="cases",
            source_type="openapi",
            raw_text="{}",
            summary_json={"title": "c"},
        )
        db.add(spec)
        db.flush()
        ep = ApiSpecEndpoint(
            spec_id=spec.id,
            project_id=pid,
            method="POST",
            path="/cases",
            summary="创建用例",
            tags_json=[],
            request_sample_json={
                "body_type": "json",
                "body": {},
                "responses": [
                    {
                        "status": "201",
                        "sample": {"id": 53, "name": "demo"},
                        "fields": [{"name": "id", "required": True}],
                    }
                ],
            },
        )
        db.add(ep)
        db.flush()
        row = db.get(ApiDefinition, did)
        assert row is not None
        row.spec_id = spec.id
        row.spec_endpoint_id = ep.id
        db.commit()
    finally:
        db.close()

    mock_llm.return_value = json.dumps(
        {
            "title": "创建用例",
            "summary": "扁平响应",
            "steps": [
                {
                    "id": "s1",
                    "definition_id": did,
                    "on_pass": "end",
                    "on_fail": "end",
                    "extract_hints": ["caseId"],
                    "assertions": [
                        {"type": "jsonpath", "expr": "$.data.caseId", "op": "exists"},
                    ],
                }
            ],
            "warnings": [],
        }
    )
    cr = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions",
        headers=h,
        json={"definition_ids": [did]},
    )
    assert cr.status_code == 201, cr.text
    plan_asserts = cr.json()["plan"]["steps"][0]["assertions"]
    assert any(a.get("expr") == "$.id" and a.get("op") == "exists" for a in plan_asserts)
    assert not any(
        isinstance(a.get("expr"), str) and a.get("expr", "").startswith("$.data.") for a in plan_asserts
    )

    conf = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{cr.json()['id']}/confirm",
        headers=h,
        json={"action": "confirm"},
    )
    assert conf.status_code == 200, conf.text
    suite_id = conf.json()["suite_id"]
    db = TestingSessionLocal()
    try:
        suite = db.get(ApiSuite, suite_id)
        assert suite is not None
        extractors = suite.steps_json[0].get("extractors") or []
        assert extractors[0]["name"] == "caseId"
        assert extractors[0]["expr"] == "$.id"
        saved = suite.steps_json[0]["assertions"]
        assert any(a.get("expr") == "$.id" for a in saved)
        assert not any(
            isinstance(a.get("expr"), str) and a.get("expr", "").startswith("$.data.") for a in saved
        )
    finally:
        db.close()


def _add_member(pid: int, username: str, role: str = "editor"):
    db = TestingSessionLocal()
    try:
        from app.models.project.project import ProjectMember

        u = db.query(User).filter(User.username == username).one()
        db.add(ProjectMember(project_id=pid, user_id=u.id, role=role))
        db.commit()
    finally:
        db.close()


@patch("app.services.api_automation.api_scenario_llm.llm.chat_complete")
def test_scenario_agent_session_list_delete_isolation(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    _user("scen_hist_a", "p")
    _user("scen_hist_b", "p")
    tok_a = _login("scen_hist_a", "p")
    tok_b = _login("scen_hist_b", "p")
    pid = _project(tok_a, "HistApi")
    _add_member(pid, "scen_hist_b", "editor")
    client = TestClient(app)
    h_a = {"Authorization": f"Bearer {tok_a}"}
    h_b = {"Authorization": f"Bearer {tok_b}"}
    did = _create_def(client, h_a, pid, "hist", "GET", "/hist")
    mock_llm.return_value = json.dumps(
        {
            "title": "t",
            "summary": "s",
            "steps": [{"id": "s1", "definition_id": did, "on_pass": "end", "on_fail": "end"}],
            "warnings": [],
        }
    )
    cr = client.post(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions",
        headers=h_a,
        json={"prompt": "串联登录与查询接口流程", "definition_ids": [did]},
    )
    assert cr.status_code == 201, cr.text
    sid = cr.json()["id"]

    r_list = client.get(f"/api/v1/projects/{pid}/api-scenario-agent/sessions", headers=h_a)
    assert r_list.status_code == 200
    items = r_list.json()
    assert len(items) == 1
    assert items[0]["id"] == sid
    assert items[0]["title"].startswith("串联登录")
    assert items[0]["status"] == "awaiting_confirm"

    r_other = client.get(f"/api/v1/projects/{pid}/api-scenario-agent/sessions", headers=h_b)
    assert r_other.status_code == 200
    assert r_other.json() == []

    r_get_other = client.get(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{sid}",
        headers=h_b,
    )
    assert r_get_other.status_code == 404

    r_del = client.delete(
        f"/api/v1/projects/{pid}/api-scenario-agent/sessions/{sid}",
        headers=h_a,
    )
    assert r_del.status_code == 204
    assert client.get(f"/api/v1/projects/{pid}/api-scenario-agent/sessions", headers=h_a).json() == []

