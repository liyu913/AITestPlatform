"""B4 stage 2: runs linked to batch items become attempts; unified execution list."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.functional.cycle_attempt_sync import sync_attempt_from_run
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _task_stub() -> MagicMock:
    """Celery enqueue stub with a string task id (SessionLocal rejects MagicMock binds)."""
    m = MagicMock()
    m.id = "task-test"
    return m


def _user(username: str, pw: str = "p") -> None:
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str = "p") -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str, name: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _batch_with_api_item(client: TestClient, pid: int, h: dict) -> tuple[int, int, int]:
    """Return (cycle_id, cycle_item_id, api_case_id)."""
    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "测试环境", "base_url": "https://api.example.com"},
    )
    assert env.status_code == 201, env.text
    api_case = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "查询订单", "path": "/orders/1"},
    )
    assert api_case.status_code == 201, api_case.text
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "第 1 批", "status": "active"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "api_case", "asset_id": api_case.json()["id"]}]},
    ).json()
    return cycle["id"], items[0]["id"], api_case.json()["id"]


@patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", return_value=_task_stub())
@patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay", return_value=_task_stub())
@patch("app.api.functional.testcases.enqueue_reindex")
def test_api_run_linked_to_batch_item_creates_idempotent_attempt(_mock, _delay, _apply):
    _user("b4link")
    tok = _login("b4link")
    pid = _project(tok, "B4LINK")
    client = TestClient(app)
    h = _auth(tok)
    cycle_id, item_id, api_case_id = _batch_with_api_item(client, pid, h)

    run = client.post(
        f"/api/v1/projects/{pid}/api-cases/{api_case_id}/run",
        headers=h,
        json={"cycle_item_id": item_id, "sync": False},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    # Simulate the executor finishing, then the adapter sync (what the Celery task does).
    db = TestingSessionLocal()
    try:
        from app.models.api_automation.api_automation import ApiRun

        row = db.get(ApiRun, run_id)
        assert row.cycle_item_id == item_id
        row.status = "passed"
        row.summary_json = {"passed": 1, "failed": 0, "error": 0, "message": "ok"}
        db.commit()
        attempt = sync_attempt_from_run(db, run_type="api", run_id=run_id)
    finally:
        db.close()

    assert attempt is not None
    assert attempt.attempt_no == 1
    assert attempt.execution_type == "api"
    assert attempt.original_run_type == "api"
    assert attempt.original_run_id == run_id
    assert attempt.status == "passed"
    assert attempt.asset_type == "api_case"

    # A duplicated callback updates the same attempt instead of appending one.
    db = TestingSessionLocal()
    try:
        again = sync_attempt_from_run(db, run_type="api", run_id=run_id)
        assert again is not None and again.id == attempt.id
        count = db.query(type(attempt)).filter_by(cycle_item_id=item_id).count()
        assert count == 1
        # A stale "running" callback must not undo a terminal result.
        from app.models.api_automation.api_automation import ApiRun

        row = db.get(ApiRun, run_id)
        row.status = "running"
        db.commit()
        stale = sync_attempt_from_run(db, run_type="api", run_id=run_id)
        assert stale is not None and stale.status == "passed"
    finally:
        db.close()

    rows = client.get(f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/", headers=h).json()
    item = next(i for i in rows["items"] if i["id"] == item_id)
    assert item["result"] == "passed"
    assert item["last_run_type"] == "api"
    assert item["last_run_id"] == run_id


@patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay", return_value=_task_stub())
@patch("app.api.functional.testcases.enqueue_reindex")
def test_run_cannot_link_to_another_projects_item(_mock, _delay):
    _user("b4link2")
    tok = _login("b4link2")
    pid = _project(tok, "B4LINK2")
    other = _project(tok, "B4LINK3")
    client = TestClient(app)
    h = _auth(tok)

    _, other_item, _ = _batch_with_api_item(client, other, h)
    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "本环境", "base_url": "https://api.example.com"},
    ).json()
    case = client.post(
        f"/api/v1/projects/{pid}/api-cases/", headers=h, json={"name": "本用例", "path": "/x"}
    ).json()
    bad = client.post(
        f"/api/v1/projects/{pid}/api-cases/{case['id']}/run",
        headers=h,
        json={"cycle_item_id": other_item, "sync": False},
    )
    assert bad.status_code == 400
    assert "执行项" in bad.json()["message"]
    assert env["id"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_sync_api_run_writes_batch_attempt(_mock, monkeypatch):
    """Synchronous executions (sync=true) must also mirror into the batch item."""
    _user("b4sync")
    tok = _login("b4sync")
    pid = _project(tok, "B4SYNC")
    client = TestClient(app)
    h = _auth(tok)
    cycle_id, item_id, api_case_id = _batch_with_api_item(client, pid, h)
    envs = client.get(f"/api/v1/projects/{pid}/api-envs/", headers=h).json()
    env_rows = envs.get("items") if isinstance(envs, dict) else envs
    env_id = env_rows[0]["id"]

    from app.services.api_automation import api_run_executor

    def fake_execute(**_kwargs):
        return {
            "status": "passed",
            "request_snapshot": {"method": "GET", "path": "/orders/1"},
            "response_status": 200,
            "response_headers": {},
            "response_body": "{}",
            "duration_ms": 3,
            "assertion_results": [{"type": "status", "ok": True, "expected": 200, "actual": 200}],
            "extract_results": {},
            "error_message": "",
        }

    monkeypatch.setattr(api_run_executor, "execute_http_case", fake_execute)

    run = client.post(
        f"/api/v1/projects/{pid}/api-cases/{api_case_id}/run",
        headers=h,
        json={"cycle_item_id": item_id, "env_id": env_id, "sync": True},
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "passed", run.text

    attempts = client.get(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item_id}/attempts", headers=h
    ).json()
    assert len(attempts) == 1, attempts
    assert attempts[0]["execution_type"] == "api"
    assert attempts[0]["status"] == "passed"
    assert attempts[0]["original_run_id"] == run.json()["id"]

    rows = client.get(f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/", headers=h).json()
    item = next(i for i in rows["items"] if i["id"] == item_id)
    assert item["result"] == "passed"
    assert item["attempt_no"] == 1


@patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", return_value=_task_stub())
@patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay", return_value=_task_stub())
@patch("app.api.functional.testcases.enqueue_reindex")
def test_unified_execution_list_merges_sources(_mock, _delay, _apply):
    _user("b4exec")
    tok = _login("b4exec")
    pid = _project(tok, "B4EXEC")
    client = TestClient(app)
    h = _auth(tok)

    # Manual item
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "手工下单", "steps": [{"order": 1, "action": "提交", "expected": "成功"}]},
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "回归计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "第 2 批", "status": "active"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case["id"]]},
    ).json()
    item_id = items[0]["id"]
    client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{item_id}/attempts",
        headers=h,
        json={"execution_type": "manual", "status": "failed", "summary": "按钮无响应"},
    )

    # API run
    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "接口环境", "base_url": "https://api.example.com"},
    ).json()
    api_case = client.post(
        f"/api/v1/projects/{pid}/api-cases/", headers=h, json={"name": "接口下单", "path": "/o"}
    ).json()
    api_run = client.post(
        f"/api/v1/projects/{pid}/api-cases/{api_case['id']}/run",
        headers=h,
        json={"sync": False},
    ).json()

    # UI run
    ui_env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "UI 环境", "base_url": "https://app.example.com"},
    ).json()
    ui_case = client.post(
        f"/api/v1/projects/{pid}/ui-cases/", headers=h, json={"name": "界面下单"}
    ).json()
    ui_run = client.post(
        f"/api/v1/projects/{pid}/ui-cases/{ui_case['id']}/run",
        headers=h,
        json={"env_id": ui_env["id"], "sync": False},
    ).json()

    listing = client.get(f"/api/v1/projects/{pid}/executions", headers=h)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    kinds = {row["kind"] for row in body["items"]}
    assert kinds == {"manual", "api", "ui"}, body
    assert body["total"] == 3
    assert body["counts"] == {"manual": 1, "api": 1, "ui": 1}

    manual = next(r for r in body["items"] if r["kind"] == "manual")
    assert manual["title"] == "手工下单"
    assert manual["status"] == "failed"
    assert manual["batch"] == "回归计划 / 第 2 批"
    assert manual["attempt_no"] == 1
    assert manual["link"] == f"cycles/{cycle['id']}?item={item_id}"

    api_row = next(r for r in body["items"] if r["kind"] == "api")
    assert api_row["id"] == api_run["id"]
    assert api_row["title"] == "接口下单"
    assert api_row["link"] == f"api-runs/{api_run['id']}"
    assert api_row["target_type"] == "api_case"

    ui_row = next(r for r in body["items"] if r["kind"] == "ui")
    assert ui_row["id"] == ui_run["id"]
    assert ui_row["link"] == f"ui-runs/{ui_run['id']}"

    # Filters: kind and status; counts always describe the whole project.
    only_api = client.get(f"/api/v1/projects/{pid}/executions?kind=api", headers=h).json()
    assert [r["kind"] for r in only_api["items"]] == ["api"]
    assert only_api["total"] == 1

    failed = client.get(f"/api/v1/projects/{pid}/executions?status=failed", headers=h).json()
    assert {r["kind"] for r in failed["items"]} == {"manual"}

    paged = client.get(f"/api/v1/projects/{pid}/executions?page=1&page_size=2", headers=h).json()
    assert len(paged["items"]) == 2 and paged["total"] == 3
    second = client.get(f"/api/v1/projects/{pid}/executions?page=2&page_size=2", headers=h).json()
    assert len(second["items"]) == 1
    assert env["id"] and ui_env["id"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_agent_once_finalize_writes_batch_attempt(_mock, monkeypatch):
    from cryptography.fernet import Fernet

    from app.config import settings
    from app.services.ai.effective_ai_settings import LlmRuntime

    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("b4agent")
    tok = _login("b4agent")
    pid = _project(tok, "B4AGENT")
    client = TestClient(app)
    h = _auth(tok)

    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "本地", "base_url": "https://app.example.com"},
    ).json()
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "登录直跑",
            "overall_expected": "进入首页",
            "steps": [{"order": 1, "action": "打开登录页", "expected": "表单可见"}],
        },
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "批次", "status": "active"},
    ).json()
    item = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case["id"]]},
    ).json()[0]
    client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/assign",
        headers=h,
        json={"item_ids": [item["id"]], "assignee_id": client.get("/api/v1/users/me", headers=h).json()["id"]},
    )

    from app.api.ui_automation import ui_scenario_agent as agent_api

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.ui_automation.ui_scenario_agent import _complete_explore

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        plan_json = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
        _complete_explore(
            db,
            row,
            plan=plan_json,
            trace=[
                {"action": "goto", "value": "/login", "status": "ok"},
                {"action": "assert_visible", "assert": "表单可见", "status": "ok"},
                {"action": "assert_text", "assert": "进入首页", "status": "ok"},
                {"action": "done", "status": "ok"},
            ],
            code="",
            engine="playwright",
            script_kind="node",
        )

    agent_api.set_explore_runner(fake_explore)
    try:
        created = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "prompt": "",
                "testcase_id": case["id"],
                "env_id": env["id"],
                "engine": "midscene",
                "execution_mode": "agent_once",
                "cycle_item_id": item["id"],
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        run_id = session["case_run_id"]
        assert run_id is not None
    finally:
        agent_api.set_explore_runner(None)

    # finalize_agent_once mirrored the run into the batch item automatically.
    attempts = client.get(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{item['id']}/attempts", headers=h
    ).json()
    assert len(attempts) == 1, attempts
    attempt = attempts[0]
    assert attempt["execution_type"] == "agent_once"
    assert attempt["original_run_type"] == "functional"
    assert attempt["original_run_id"] == run_id
    assert attempt["status"] == "passed"

    rows = client.get(f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/", headers=h).json()
    item_row = next(i for i in rows["items"] if i["id"] == item["id"])
    assert item_row["result"] == "passed"
    assert item_row["last_run_type"] == "functional"
    assert item_row["attempt_no"] == 1
