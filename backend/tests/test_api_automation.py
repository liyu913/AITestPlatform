from unittest.mock import MagicMock, patch
import json
import re

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.api_automation.api_runner import (
    MissingVariableError,
    apply_extractors,
    execute_http_case,
    render_template,
    resolve_jsonpath,
    run_assertions,
)
from app.services.api_automation.api_url_guard import UnsafeApiUrlError, join_base_and_path
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


def _project(token: str, name: str = "API") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_join_base_and_path_ok():
    assert join_base_and_path("https://example.com/v1", "/users") == "https://example.com/v1/users"
    assert join_base_and_path("https://example.com", "/users") == "https://example.com/users"


def test_join_rejects_absolute_path():
    with pytest.raises(UnsafeApiUrlError):
        join_base_and_path("https://example.com", "https://evil.com/x")


def test_render_and_jsonpath():
    assert render_template("hi {{name}}", {"name": "a"}) == "hi a"
    with pytest.raises(MissingVariableError):
        render_template("{{missing}}", {})
    assert resolve_jsonpath({"data": {"token": "t1"}}, "$.data.token") == "t1"
    assert apply_extractors(
        [{"name": "token", "from": "jsonpath", "expr": "$.data.token"}],
        {"data": {"token": "abc"}},
    ) == {"token": "abc"}
    asserts = run_assertions(
        [{"type": "status", "expected": 200}, {"type": "jsonpath", "expr": "$.code", "op": "eq", "expected": 0}],
        status_code=200,
        body_text='{"code":0}',
        body_json={"code": 0},
        headers={"content-type": "application/json"},
        duration_ms=10,
    )
    assert all(a["ok"] for a in asserts)


def test_execute_http_case_with_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ping"
        return httpx.Response(200, json={"ok": True, "code": 0})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = execute_http_case(
            method="GET",
            base_url="https://api.example.com",
            path="/ping",
            query={},
            headers={},
            body_type="none",
            body_text="",
            timeout_ms=5000,
            assertions=[{"type": "status", "expected": 200}, {"type": "jsonpath", "expr": "$.code", "expected": 0}],
            extractors=[{"name": "ok", "from": "jsonpath", "expr": "$.ok"}],
            ctx={},
            client=client,
        )
    assert result["status"] == "passed"
    assert result["extract_results"]["ok"] == "True"


def test_env_case_run_project_scoped(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("api_a", "p")
    tok = _login("api_a", "p")
    pid = _project(tok, "P1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    # create env
    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={
            "name": "local",
            "base_url": "https://httpbin.test",
            "variables": {"user": "u1"},
            "secrets": {"password": "secret"},
            "headers": {"X-App": "raina"},
            "is_default": True,
        },
    )
    assert er.status_code == 201, er.text
    env = er.json()
    assert "password" in env["secret_keys"]
    assert "secrets" not in env

    # create case
    cr = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "ping",
            "method": "GET",
            "path": "/get",
            "assertions": [{"type": "status", "expected": 200}],
            "status": "ready",
        },
    )
    assert cr.status_code == 201, cr.text
    case_id = cr.json()["id"]

    executed = []

    def fake_delay(run_id: int):
        from app.services.api_automation.api_run_executor import execute_api_run
        from tests.conftest import TestingSessionLocal

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)

        db = TestingSessionLocal()
        try:
            # patch execute_http_case client via monkeypatch on module used inside executor
            import app.services.api_automation.api_runner as runner_mod

            real_execute = runner_mod.execute_http_case

            def wrapped(**kwargs):
                kwargs = dict(kwargs)
                kwargs["client"] = httpx.Client(transport=transport)
                return real_execute(**kwargs)

            with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
                execute_api_run(db, run_id)
            executed.append(run_id)
        finally:
            db.close()

        m = MagicMock()
        m.id = f"task-{run_id}"
        return m

    with patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay", side_effect=fake_delay):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-cases/{case_id}/run",
            headers=h,
            json={},
        )
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]
    assert executed == [run_id]

    gr = client.get(f"/api/v1/projects/{pid}/api-runs/{run_id}", headers=h)
    assert gr.status_code == 200
    assert gr.json()["status"] == "passed"

    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{run_id}/steps", headers=h)
    assert steps.status_code == 200
    assert len(steps.json()) == 1
    assert steps.json()[0]["status"] == "passed"

    # cross-project isolation
    _user("api_b", "p")
    tok_b = _login("api_b", "p")
    pid_b = _project(tok_b, "P2")
    h_b = {"Authorization": f"Bearer {tok_b}"}
    cross = client.get(f"/api/v1/projects/{pid_b}/api-cases/{case_id}", headers=h_b)
    assert cross.status_code == 404
    cross_run = client.get(f"/api/v1/projects/{pid_b}/api-runs/{run_id}", headers=h_b)
    assert cross_run.status_code == 404


def test_batch_run_continues_after_failure(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("api_batch", "p")
    tok = _login("api_batch", "p")
    pid = _project(tok, "Batch")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://httpbin.test", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    def create_case(name: str, path: str) -> int:
        cr = client.post(
            f"/api/v1/projects/{pid}/api-cases/",
            headers=h,
            json={
                "name": name,
                "method": "GET",
                "path": path,
                "assertions": [{"type": "status", "expected": 200}],
                "status": "ready",
            },
        )
        assert cr.status_code == 201, cr.text
        return cr.json()["id"]

    ok_id = create_case("ok", "/ok")
    bad_id = create_case("bad", "/bad")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bad"):
            return httpx.Response(500, json={"ok": False})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real_execute = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real_execute(**kwargs)

    with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-cases/batch-run",
            headers=h,
            json={"case_ids": [ok_id, bad_id, ok_id], "env_id": env_id, "sync": True},
        )
    assert rr.status_code == 201, rr.text
    body = rr.json()
    assert body["trigger_type"] == "batch"
    assert body["target_id"] == 0
    assert body["status"] == "failed"
    assert body["summary_json"]["case_ids"] == [ok_id, bad_id]
    assert body["summary_json"]["passed"] == 1
    assert body["summary_json"]["failed"] == 1

    runs = client.get(f"/api/v1/projects/{pid}/api-runs/", headers=h)
    assert runs.status_code == 200
    assert runs.json()["total"] == 1

    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{body['id']}/steps", headers=h)
    assert steps.status_code == 200
    step_rows = steps.json()
    assert len(step_rows) == 2
    assert [s["case_id"] for s in step_rows] == [ok_id, bad_id]
    assert step_rows[0]["status"] == "passed"
    assert step_rows[1]["status"] == "failed"


def test_debug_sync(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_dbg", "p")
    tok = _login("api_dbg", "p")
    pid = _project(tok, "Dbg")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0})

    transport = httpx.MockTransport(handler)

    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with patch("app.api.api_automation.api_cases.execute_http_case", side_effect=wrapped):
        r = client.post(
            f"/api/v1/projects/{pid}/api-cases/debug",
            headers=h,
            json={
                "env_id": env_id,
                "method": "GET",
                "path": "/ping",
                "assertions": [{"type": "status", "expected": 200}],
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "passed"


def test_suite_sync_variable_pass(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_suite", "p")
    tok = _login("api_suite", "p")
    pid = _project(tok, "Suite")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    c1 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "login",
            "method": "POST",
            "path": "/login",
            "body_type": "json",
            "body": "{}",
            "assertions": [{"type": "status", "expected": 200}],
            "extractors": [{"name": "token", "from": "jsonpath", "expr": "$.token"}],
        },
    )
    assert c1.status_code == 201
    c2 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "me",
            "method": "GET",
            "path": "/me",
            "headers": {"Authorization": "Bearer {{token}}"},
            "assertions": [{"type": "status", "expected": 200}],
        },
    )
    assert c2.status_code == 201
    id1, id2 = c1.json()["id"], c2.json()["id"]

    sr = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={"name": "auth flow", "case_ids": [id1, id2], "env_id": env_id, "stop_on_fail": True},
    )
    assert sr.status_code == 201, sr.text
    suite_id = sr.json()["id"]

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": "abc123"})
        auth = request.headers.get("authorization", "")
        assert auth == "Bearer abc123"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-suites/{suite_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    assert rr.json()["status"] == "passed"
    assert len(calls) == 2

    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{rr.json()['id']}/steps", headers=h)
    assert steps.status_code == 200
    assert len(steps.json()) == 2
    assert steps.json()[0]["extract_results_json"]["token"] == "abc123"


def test_suite_step_extract_overrides(monkeypatch):
    """Step-level extract_overrides override case extractors during suite run."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_suite_ov", "p")
    tok = _login("api_suite_ov", "p")
    pid = _project(tok, "SuiteOv")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    c1 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "login",
            "method": "POST",
            "path": "/login",
            "body_type": "json",
            "body": "{}",
            "assertions": [{"type": "status", "expected": 200}],
            "extractors": [{"name": "token", "from": "jsonpath", "expr": "$.wrong"}],
        },
    )
    assert c1.status_code == 201
    c2 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "me",
            "method": "GET",
            "path": "/me",
            "headers": {"Authorization": "Bearer {{token}}"},
            "assertions": [{"type": "status", "expected": 200}],
        },
    )
    assert c2.status_code == 201
    id1, id2 = c1.json()["id"], c2.json()["id"]

    sr = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={
            "name": "override flow",
            "env_id": env_id,
            "stop_on_fail": True,
            "steps": [
                {
                    "id": "s1",
                    "case_id": id1,
                    "on_pass": "s2",
                    "on_fail": "end",
                    "extract_overrides": [
                        {"name": "token", "type": "jsonpath", "expr": "$.data.token"},
                    ],
                },
                {"id": "s2", "case_id": id2, "on_pass": "end", "on_fail": "end"},
            ],
        },
    )
    assert sr.status_code == 201, sr.text
    suite = sr.json()
    assert suite["steps"][0]["extract_overrides"][0]["expr"] == "$.data.token"
    suite_id = suite["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"data": {"token": "from-override"}})
        assert request.headers.get("authorization") == "Bearer from-override"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-suites/{suite_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    assert rr.json()["status"] == "passed"
    steps = client.get(f"/api/v1/projects/{pid}/api-runs/{rr.json()['id']}/steps", headers=h)
    assert steps.json()[0]["extract_results_json"]["token"] == "from-override"


def test_suite_inline_step_no_case(monkeypatch):
    """Suite steps with inline request payload execute without case_id."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_suite_inline", "p")
    tok = _login("api_suite_inline", "p")
    pid = _project(tok, "SuiteInline")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    sr = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={
            "name": "inline only",
            "env_id": env_id,
            "stop_on_fail": True,
            "case_ids": [],
            "steps": [
                {
                    "id": "s1",
                    "name": "ping",
                    "method": "GET",
                    "path": "/ping",
                    "assertions": [{"type": "status", "expected": 200}],
                    "extractors": [{"name": "msg", "type": "jsonpath", "expr": "$.msg"}],
                    "on_pass": "s2",
                    "on_fail": "end",
                },
                {
                    "id": "s2",
                    "name": "echo",
                    "method": "GET",
                    "path": "/echo",
                    "headers": {"X-Msg": "{{msg}}"},
                    "assertions": [{"type": "status", "expected": 200}],
                    "on_pass": "end",
                    "on_fail": "end",
                },
            ],
        },
    )
    assert sr.status_code == 201, sr.text
    suite = sr.json()
    assert suite["case_ids"] == []
    assert suite["steps"][0]["method"] == "GET"
    assert "case_id" not in suite["steps"][0] or suite["steps"][0].get("case_id") is None
    suite_id = suite["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ping"):
            return httpx.Response(200, json={"msg": "hello"})
        assert request.headers.get("x-msg") == "hello"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-suites/{suite_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    assert rr.json()["status"] == "passed"


def test_suite_step_assert_overrides(monkeypatch):
    """Step-level assert_overrides merge/replace case assertions during suite run."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_suite_assert_ov", "p")
    tok = _login("api_suite_assert_ov", "p")
    pid = _project(tok, "SuiteAssertOv")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    c1 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "ping",
            "method": "GET",
            "path": "/ping",
            "assertions": [{"type": "status", "expected": 500}],
        },
    )
    assert c1.status_code == 201
    cid = c1.json()["id"]

    sr = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={
            "name": "assert override",
            "env_id": env_id,
            "stop_on_fail": True,
            "steps": [
                {
                    "id": "s1",
                    "case_id": cid,
                    "on_pass": "end",
                    "on_fail": "end",
                    "assert_overrides": [{"type": "status", "expected": 200}],
                },
            ],
        },
    )
    assert sr.status_code == 201, sr.text
    assert sr.json()["steps"][0]["assert_overrides"][0]["expected"] == 200
    suite_id = sr.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-suites/{suite_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    assert rr.json()["status"] == "passed"


def test_stronger_assertions_and_script():
    from app.services.api_automation.api_scripts import ScriptError, run_api_script

    body = '{"code":0,"msg":"ok"}'
    asserts = run_assertions(
        [
            {"type": "contains", "expected": "ok"},
            {"type": "regex", "expected": r'"code"\s*:\s*0'},
            {
                "type": "header",
                "expr": "content-type",
                "op": "eq",
                "expected": "application/json",
            },
            {
                "type": "json_schema",
                "expected": {"type": "object", "required": ["code"], "properties": {"code": {"type": "integer"}}},
            },
        ],
        status_code=200,
        body_text=body,
        body_json={"code": 0, "msg": "ok"},
        headers={"content-type": "application/json"},
        duration_ms=5,
    )
    assert all(a["ok"] for a in asserts)

    ctx: dict[str, str] = {}
    run_api_script("set_var('x', 1+1)", ctx=ctx)
    assert ctx["x"] == "2"
    with pytest.raises(ScriptError):
        run_api_script("open('/etc/passwd')", ctx={})


def test_openapi_parse_and_postman_roundtrip():
    from app.services.api_automation.openapi_import import parse_openapi
    from app.services.api_automation.postman_io import export_postman_collection, import_postman_collection

    raw = """
    {
      "openapi": "3.0.0",
      "info": {"title": "Demo", "version": "1"},
      "paths": {
        "/ping": {
          "get": {"summary": "Ping", "operationId": "ping"}
        }
      }
    }
    """
    summary, eps = parse_openapi(raw)
    assert summary["title"] == "Demo"
    assert summary["source"] == "openapi3"
    assert len(eps) == 1
    assert eps[0]["method"] == "GET"
    assert eps[0]["path"] == "/ping"

    cyclic = """
    {
      "openapi": "3.0.0",
      "info": {"title": "Tree", "version": "1"},
      "components": {
        "schemas": {
          "Node": {
            "type": "object",
            "properties": {
              "name": {"type": "string"},
              "children": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/Node"}
              }
            }
          }
        }
      },
      "paths": {
        "/tree": {
          "post": {
            "summary": "Create tree",
            "requestBody": {
              "content": {
                "application/json": {
                  "schema": {"$ref": "#/components/schemas/Node"}
                }
              }
            },
            "responses": {
              "200": {
                "description": "ok",
                "content": {
                  "application/json": {
                    "schema": {"$ref": "#/components/schemas/Node"}
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    c_sum, c_eps = parse_openapi(cyclic)
    assert c_sum["endpoint_count"] == 1
    body = c_eps[0]["request_sample"]["body"]
    assert isinstance(body, dict)
    assert isinstance(body.get("name"), str) and body["name"] and body["name"] != "string"
    # circular children should not recurse forever
    kids = body.get("children")
    assert kids in ([], [None]) or (
        isinstance(kids, list) and (not kids or kids[0] is None or isinstance(kids[0], dict))
    )

    swagger2 = """
    {
      "swagger": "2.0",
      "info": {"title": "Pet", "version": "1.0"},
      "host": "petstore.example.com",
      "basePath": "/v2",
      "schemes": ["https"],
      "definitions": {
        "Pet": {
          "type": "object",
          "properties": {"name": {"type": "string"}, "id": {"type": "integer"}}
        }
      },
      "paths": {
        "/pets": {
          "post": {
            "summary": "Add pet",
            "parameters": [
              {
                "in": "body",
                "name": "body",
                "schema": {"$ref": "#/definitions/Pet"}
              },
              {"in": "query", "name": "dryRun", "type": "boolean", "default": false}
            ]
          },
          "get": {
            "summary": "List pets",
            "parameters": [{"in": "query", "name": "limit", "type": "integer", "default": 10}]
          }
        }
      }
    }
    """
    s2_sum, s2_eps = parse_openapi(swagger2)
    assert s2_sum["source"] == "swagger2"
    assert s2_sum["base_url"] == "https://petstore.example.com/v2"
    assert len(s2_eps) == 2
    post = next(e for e in s2_eps if e["method"] == "POST")
    assert post["request_sample"]["body_type"] == "json"
    pet_name = post["request_sample"]["body"]["name"]
    assert isinstance(pet_name, str) and pet_name and pet_name != "string"
    assert post["request_sample"]["query"]["dryRun"] == "False"
    get = next(e for e in s2_eps if e["method"] == "GET")
    assert get["request_sample"]["query"]["limit"] == "10"

    oas3 = """
    {
      "openapi": "3.0.0",
      "info": {"title": "P", "version": "1"},
      "paths": {
        "/items/{id}": {
          "get": {
            "summary": "Get item",
            "operationId": "getItem",
            "tags": ["items"],
            "parameters": [
              {"name": "id", "in": "path", "required": true, "schema": {"type": "integer", "example": 7}},
              {"name": "verbose", "in": "query", "schema": {"type": "boolean", "default": false}}
            ]
          }
        }
      }
    }
    """
    _, o3 = parse_openapi(oas3)
    assert o3[0]["path"] == "/items/{{id}}"
    assert o3[0]["request_sample"]["path_params"]["id"] == "7"
    assert o3[0]["request_sample"]["query"]["verbose"] == "False"

    apartment = """
    {
      "openapi": "3.0.0",
      "info": {"title": "Apt", "version": "1"},
      "paths": {
        "/apartments": {
          "post": {
            "summary": "Create apartment",
            "requestBody": {
              "content": {
                "application/json": {
                  "schema": {
                    "type": "object",
                    "properties": {
                      "name": {"type": "string"},
                      "contactPhone": {"type": "string"},
                      "createTime": {"type": "string", "format": "date-time"},
                      "updateTime": {"type": "string"},
                      "birthday": {"type": "string", "format": "date"},
                      "email": {"type": "string", "format": "email"},
                      "status": {"type": "string", "enum": ["active", "off"]}
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    _, apt_eps = parse_openapi(apartment)
    apt_body = apt_eps[0]["request_sample"]["body"]
    assert isinstance(apt_body["name"], str) and apt_body["name"] and apt_body["name"] != "string"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", apt_body["createTime"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", apt_body["updateTime"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", apt_body["birthday"])
    assert re.match(r"^user\d{4}@example\.com$", apt_body["email"])
    assert apt_body["status"] in {"active", "off"}
    assert re.match(r"^138\d{8}$", apt_body["contactPhone"])

    cases = [
        {
            "name": "Ping",
            "method": "GET",
            "path": "/ping",
            "query": {"q": "1"},
            "headers": {"X-A": "b"},
            "body_type": "none",
            "body": "",
        },
        {
            "name": "Create",
            "method": "POST",
            "path": "/items",
            "query": {},
            "headers": {},
            "body_type": "json",
            "body": '{"a":1}',
        },
    ]
    coll = export_postman_collection(name="Demo", cases=cases, base_url="https://api.example.com")
    imported = import_postman_collection(json.dumps(coll))
    assert len(imported) == 2
    assert imported[0]["method"] == "GET"
    assert imported[0]["path"] == "/ping"
    assert imported[1]["body_type"] == "json"


def test_normalize_path_template_idempotent():
    from app.services.api_automation.openapi_import import (
        endpoint_to_case_payload,
        normalize_path_template,
    )

    assert normalize_path_template("/items/{id}") == "/items/{{id}}"
    assert normalize_path_template("/items/{{id}}") == "/items/{{id}}"
    assert (
        normalize_path_template("/p/{project_id}/r/{requirement_id}")
        == "/p/{{project_id}}/r/{{requirement_id}}"
    )
    again = normalize_path_template("/p/{{project_id}}/r/{{requirement_id}}")
    assert again == "/p/{{project_id}}/r/{{requirement_id}}"
    payload = endpoint_to_case_payload(
        {"method": "GET", "path": "/items/{id}", "summary": "Get", "request_sample": {}}
    )
    assert payload["path"] == "/items/{{id}}"


def test_upsert_definition_upgrades_legacy_path_braces():
    from app.models.api_automation.api_automation import ApiDefinition, ApiSpec, ApiSpecEndpoint
    from app.services.api_automation import api_definitions as def_svc

    _user("path_brace", "p")
    tok = _login("path_brace", "p")
    pid = _project(tok, "PathBrace")
    db = TestingSessionLocal()
    try:
        spec = ApiSpec(
            project_id=pid,
            name="legacy",
            source_type="openapi",
            raw_text="{}",
            summary_json={},
            creator_id=None,
        )
        db.add(spec)
        db.flush()
        legacy = ApiDefinition(
            project_id=pid,
            name="Get item",
            method="GET",
            path="/items/{id}",
            source="openapi",
            status="active",
            query_json={},
            headers_json={},
            body_type="none",
            body_text="",
            tags_json=[],
        )
        db.add(legacy)
        db.flush()
        ep = ApiSpecEndpoint(
            spec_id=spec.id,
            project_id=pid,
            method="GET",
            path="/items/{{id}}",
            summary="Get item",
            tags_json=["items"],
            request_sample_json={"body_type": "none", "query": {}, "path_params": {"id": "7"}},
        )
        db.add(ep)
        db.flush()
        row, created = def_svc.upsert_definition_from_endpoint(
            db, project_id=pid, ep=ep, creator_id=None
        )
        assert created is False
        assert row.id == legacy.id
        assert row.path == "/items/{{id}}"
        n = (
            db.query(ApiDefinition)
            .filter(ApiDefinition.project_id == pid, ApiDefinition.method == "GET")
            .count()
        )
        assert n == 1
        case = def_svc.definition_to_case(
            db, project_id=pid, definition=legacy, creator_id=None
        )
        assert case.path == "/items/{{id}}"
        db.commit()
    finally:
        db.close()


def test_openapi_create_product_field_tables():
    from pathlib import Path

    from app.services.api_automation.openapi_import import parse_openapi

    raw = Path(__file__).resolve().parents[2] / "examples" / "demo-openapi.yaml"
    summary, eps = parse_openapi(raw.read_text(encoding="utf-8"))
    assert summary["title"] == "Demo Shop API"
    ep = next(e for e in eps if e.get("operation_id") == "createProduct")
    sample = ep["request_sample"]
    fields = {f["name"]: f for f in sample["request_fields"]}
    assert fields["name"]["required"] is True
    assert fields["name"]["type"] == "string"
    assert fields["name"]["description"] == "商品名称"
    assert fields["price"]["required"] is True
    assert fields["price"]["description"] == "价格"
    resp = next(r for r in sample["responses"] if r["status"] == "201")
    assert resp["description"]
    resp_fields = {f["name"]: f for f in resp["fields"]}
    assert "id" in resp_fields
    assert resp["sample"]["id"] == "1001"

    list_ep = next(e for e in eps if e.get("operation_id") == "listProducts")
    params = {p["name"]: p for p in list_ep["request_sample"]["parameters"]}
    assert params["page"]["in"] == "query"
    assert params["page"]["example"] == "1"


def test_spec_reparse_refreshes_detail_fields(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_reparse", "p")
    tok = _login("api_reparse", "p")
    pid = _project(tok, "Reparse")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    raw = json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/items": {
                    "post": {
                        "summary": "Create",
                        "operationId": "createItem",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "description": "名称",
                                                "example": "a",
                                            }
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer", "example": 1}},
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    )
    # Simulate legacy import: store raw but empty detail sample
    from app.models.api_automation.api_automation import ApiSpec, ApiSpecEndpoint
    from tests.conftest import TestingSessionLocal

    db = TestingSessionLocal()
    try:
        spec = ApiSpec(
            project_id=pid,
            name="legacy",
            source_type="openapi",
            raw_text=raw,
            summary_json={"title": "T", "endpoint_count": 1},
        )
        db.add(spec)
        db.flush()
        db.add(
            ApiSpecEndpoint(
                spec_id=spec.id,
                project_id=pid,
                method="POST",
                path="/items",
                operation_id="createItem",
                summary="Create",
                tags_json=[],
                request_sample_json={"body_type": "json", "body": {"name": "a"}},
            )
        )
        db.commit()
        sid = spec.id
    finally:
        db.close()

    before = client.get(f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints", headers=h)
    assert before.status_code == 200
    assert "responses" not in (before.json()["items"][0].get("request_sample_json") or {})

    rr = client.post(f"/api/v1/projects/{pid}/api-specs/{sid}/reparse", headers=h)
    assert rr.status_code == 200, rr.text
    after = client.get(f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints", headers=h)
    sample = after.json()["items"][0]["request_sample_json"]
    assert sample["request_fields"][0]["description"] == "名称"
    assert sample["responses"][0]["status"] == "201"


def test_report_export_shapes():
    from app.services.api_automation.api_report import build_html_report, build_junit_xml

    steps = [
        {
            "order": 1,
            "case_id": 1,
            "case_name": "ok",
            "status": "passed",
            "duration_ms": 12,
            "response_status": 200,
            "error_message": "",
            "assertion_results_json": [{"ok": True, "detail": "status 200"}],
            "response_body_text": "{}",
        },
        {
            "order": 2,
            "case_id": 2,
            "case_name": "bad",
            "status": "failed",
            "duration_ms": 8,
            "response_status": 500,
            "error_message": "assertion failed",
            "assertion_results_json": [{"ok": False, "detail": "status 500 == 200"}],
            "response_body_text": "err",
        },
    ]
    junit = build_junit_xml(run_id=9, project_id=1, status="failed", steps=steps)
    assert "testcase" in junit and "failure" in junit
    html = build_html_report(run_id=9, project_id=1, status="failed", summary={"passed": 1, "failed": 1}, steps=steps)
    assert "bad" in html and "failed" in html


def test_schedule_next_run_utc():
    from datetime import datetime, timezone

    from app.services.api_automation.api_schedule_util import next_run_iso

    base = datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc)
    nxt = next_run_iso("0 * * * *", base)
    assert nxt.startswith("2026-10-01T01:00:00")


def test_openapi_import_api_and_export_run(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_assets", "p")
    tok = _login("api_assets", "p")
    pid = _project(tok, "Assets")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "e", "base_url": "https://svc.test", "is_default": True},
    )
    assert er.status_code == 201

    spec = client.post(
        f"/api/v1/projects/{pid}/api-specs/import",
        headers=h,
        json={
            "name": "Demo",
            "content": json.dumps(
                {
                    "openapi": "3.0.0",
                    "info": {"title": "Demo", "version": "1"},
                    "paths": {"/hello": {"get": {"summary": "Hello"}}},
                }
            ),
        },
    )
    assert spec.status_code == 201, spec.text
    sid = spec.json()["id"]
    eps = client.get(f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints", headers=h)
    assert eps.status_code == 200
    assert eps.json()["total"] == 1
    assert len(eps.json()["items"]) == 1
    ep_id = eps.json()["items"][0]["id"]

    imported = client.post(
        f"/api/v1/projects/{pid}/api-specs/{sid}/import-cases",
        headers=h,
        json={"endpoint_ids": [ep_id], "status": "draft"},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["count"] == 1
    case_id = imported.json()["created_ids"][0]
    case_get = client.get(f"/api/v1/projects/{pid}/api-cases/{case_id}", headers=h)
    assert case_get.status_code == 200
    assert case_get.json().get("api_id") is not None

    defs = client.get(f"/api/v1/projects/{pid}/api-definitions/?page=1&page_size=20", headers=h)
    assert defs.status_code == 200
    assert defs.json()["total"] >= 1

    pm = client.post(
        f"/api/v1/projects/{pid}/api-postman/export",
        headers=h,
        json={"name": "X", "base_url": "https://svc.test"},
    )
    assert pm.status_code == 200
    assert "item" in pm.json()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    import app.services.api_automation.api_runner as runner_mod

    real = runner_mod.execute_http_case

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        kwargs["client"] = httpx.Client(transport=transport)
        return real(**kwargs)

    with patch("app.services.api_automation.api_run_executor.execute_http_case", side_effect=wrapped):
        rr = client.post(
            f"/api/v1/projects/{pid}/api-cases/{case_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 201, rr.text
    rid = rr.json()["id"]
    junit = client.get(f"/api/v1/projects/{pid}/api-runs/{rid}/export?format=junit", headers=h)
    assert junit.status_code == 200
    assert "testcase" in junit.text
    html = client.get(f"/api/v1/projects/{pid}/api-runs/{rid}/export?format=html", headers=h)
    assert html.status_code == 200
    assert "html" in html.text.lower()


def test_api_spec_endpoints_page_and_filters(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_spec_filter", "p")
    tok = _login("api_spec_filter", "p")
    pid = _project(tok, "SpecFilter")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    content = {
        "openapi": "3.0.0",
        "info": {"title": "Filter Demo", "version": "1"},
        "paths": {
            "/users": {
                "get": {"summary": "List users", "operationId": "listUsers", "tags": ["users"]},
                "post": {"summary": "Create user", "operationId": "createUser", "tags": ["users", "write"]},
            },
            "/orders": {
                "get": {"summary": "List orders", "operationId": "listOrders", "tags": ["orders"]},
            },
            "/health": {
                "get": {"summary": "Health", "operationId": "health", "tags": ["ops"]},
            },
        },
    }
    spec = client.post(
        f"/api/v1/projects/{pid}/api-specs/import",
        headers=h,
        json={"name": "FilterDoc", "content": json.dumps(content)},
    )
    assert spec.status_code == 201, spec.text
    sid = spec.json()["id"]

    listed = client.get(
        f"/api/v1/projects/{pid}/api-specs/?page=1&page_size=10&search=Filter",
        headers=h,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    assert any(s["id"] == sid for s in listed.json()["items"])

    all_eps = client.get(
        f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints?page=1&page_size=2",
        headers=h,
    )
    assert all_eps.status_code == 200
    body = all_eps.json()
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2

    page2 = client.get(
        f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints?page=2&page_size=2",
        headers=h,
    )
    assert page2.status_code == 200
    assert len(page2.json()["items"]) == 2
    assert {x["id"] for x in body["items"]}.isdisjoint({x["id"] for x in page2.json()["items"]})

    by_method = client.get(
        f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints?method=POST",
        headers=h,
    )
    assert by_method.status_code == 200
    assert by_method.json()["total"] == 1
    assert by_method.json()["items"][0]["method"] == "POST"

    by_search = client.get(
        f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints?search=orders",
        headers=h,
    )
    assert by_search.status_code == 200
    assert by_search.json()["total"] == 1
    assert by_search.json()["items"][0]["path"] == "/orders"

    by_tag = client.get(
        f"/api/v1/projects/{pid}/api-specs/{sid}/endpoints?tag=write",
        headers=h,
    )
    assert by_tag.status_code == 200
    assert by_tag.json()["total"] == 1
    assert by_tag.json()["items"][0]["operation_id"] == "createUser"

    tags = client.get(f"/api/v1/projects/{pid}/api-specs/{sid}/endpoint-tags", headers=h)
    assert tags.status_code == 200
    assert tags.json() == ["ops", "orders", "users", "write"]


def test_api_definition_crud_and_create_case(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_defs", "p")
    tok = _login("api_defs", "p")
    pid = _project(tok, "Defs")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    created = client.post(
        f"/api/v1/projects/{pid}/api-definitions/",
        headers=h,
        json={
            "name": "List users",
            "module": "用户",
            "method": "GET",
            "path": "/users",
            "body_type": "none",
        },
    )
    assert created.status_code == 201, created.text
    api_id = created.json()["id"]
    assert created.json()["module"] == "用户"

    dup = client.post(
        f"/api/v1/projects/{pid}/api-definitions/",
        headers=h,
        json={"name": "x", "method": "GET", "path": "/users"},
    )
    assert dup.status_code == 409

    case = client.post(
        f"/api/v1/projects/{pid}/api-definitions/{api_id}/create-case",
        headers=h,
        json={"status": "draft"},
    )
    assert case.status_code == 201, case.text
    case_id = case.json()["case_id"]
    got = client.get(f"/api/v1/projects/{pid}/api-cases/{case_id}", headers=h)
    assert got.json()["api_id"] == api_id
    assert got.json()["path"] == "/users"

    ensure1 = client.post(
        f"/api/v1/projects/{pid}/api-definitions/{api_id}/ensure-case",
        headers=h,
    )
    assert ensure1.status_code == 200, ensure1.text
    assert ensure1.json()["case_id"] == case_id
    assert ensure1.json()["created"] is False

    # second definition without cases → creates draft once, then reuses
    created2 = client.post(
        f"/api/v1/projects/{pid}/api-definitions/",
        headers=h,
        json={
            "name": "Create user",
            "method": "POST",
            "path": "/users",
            "body_type": "json",
            "body": "{}",
        },
    )
    assert created2.status_code == 201, created2.text
    api_id2 = created2.json()["id"]
    ensure_new = client.post(
        f"/api/v1/projects/{pid}/api-definitions/{api_id2}/ensure-case",
        headers=h,
    )
    assert ensure_new.status_code == 200, ensure_new.text
    assert ensure_new.json()["created"] is True
    cid2 = ensure_new.json()["case_id"]
    ensure_again = client.post(
        f"/api/v1/projects/{pid}/api-definitions/{api_id2}/ensure-case",
        headers=h,
    )
    assert ensure_again.json()["case_id"] == cid2
    assert ensure_again.json()["created"] is False

    # rule-based multi-case generation
    gen = client.post(
        f"/api/v1/projects/{pid}/api-definitions/{api_id2}/generate-cases",
        headers=h,
        json={"status": "draft", "max_cases": 12},
    )
    assert gen.status_code == 201, gen.text
    assert gen.json()["count"] >= 2
    assert len(gen.json()["case_ids"]) == gen.json()["count"]
    names = gen.json()["names"]
    assert any("正向" in n for n in names)

    modules = client.get(f"/api/v1/projects/{pid}/api-definitions/modules", headers=h)
    assert modules.status_code == 200
    assert "用户" in modules.json()


def test_list_filters_search_and_status(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("api_filt", "p")
    tok = _login("api_filt", "p")
    pid = _project(tok, "Filt")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "AlphaEnv", "base_url": "https://httpbin.org", "is_default": True},
    )
    assert env.status_code == 201, env.text
    client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "BetaEnv", "base_url": "https://httpbin.org"},
    )

    searched = client.get(f"/api/v1/projects/{pid}/api-envs/?search=Alpha", headers=h)
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["name"] == "AlphaEnv"

    suite = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={"name": "SmokeSuite", "case_ids": [], "stop_on_fail": True},
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]
    ss = client.get(f"/api/v1/projects/{pid}/api-suites/?search=Smoke", headers=h)
    assert ss.status_code == 200
    assert ss.json()["total"] == 1

    sch = client.post(
        f"/api/v1/projects/{pid}/api-schedules/",
        headers=h,
        json={
            "name": "NightlyJob",
            "suite_id": suite_id,
            "cron_expr": "0 * * * *",
            "enabled": True,
        },
    )
    assert sch.status_code == 201, sch.text
    sc = client.get(f"/api/v1/projects/{pid}/api-schedules/?search=Nightly", headers=h)
    assert sc.status_code == 200
    assert sc.json()["total"] == 1

    runs = client.get(f"/api/v1/projects/{pid}/api-runs/?status=pending", headers=h)
    assert runs.status_code == 200
    assert "items" in runs.json()


def test_schedule_runs_filtered_by_schedule_id():
    from app.models.api_automation.api_automation import ApiRun

    _user("sched_runs", "p")
    tok = _login("sched_runs", "p")
    pid = _project(tok, "SchedRuns")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    suite = client.post(
        f"/api/v1/projects/{pid}/api-suites/",
        headers=h,
        json={"name": "S", "case_ids": [], "stop_on_fail": True},
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]

    def create_schedule(name: str) -> int:
        res = client.post(
            f"/api/v1/projects/{pid}/api-schedules/",
            headers=h,
            json={"name": name, "suite_id": suite_id, "cron_expr": "0 * * * *", "enabled": True},
        )
        assert res.status_code == 201, res.text
        return res.json()["id"]

    mine = create_schedule("Mine")
    other = create_schedule("Other")
    db = TestingSessionLocal()
    try:
        db.add(
            ApiRun(
                project_id=pid,
                trigger_type="suite",
                target_id=suite_id,
                status="passed",
                summary_json={"trigger": "schedule", "schedule_id": mine},
                started_at="2026-10-19T10:33:59+00:00",
                finished_at="2026-10-19T10:34:01+00:00",
            )
        )
        db.add(
            ApiRun(
                project_id=pid,
                trigger_type="suite",
                target_id=suite_id,
                status="failed",
                summary_json={"trigger": "schedule", "schedule_id": other},
            )
        )
        db.commit()
    finally:
        db.close()

    listed = client.get(f"/api/v1/projects/{pid}/api-schedules/{mine}/runs", headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["summary_json"]["schedule_id"] == mine
    assert body["items"][0]["status"] == "passed"

    others = client.get(f"/api/v1/projects/{pid}/api-schedules/{other}/runs", headers=h)
    assert others.status_code == 200, others.text
    assert others.json()["total"] == 1
    assert others.json()["items"][0]["summary_json"]["schedule_id"] == other
