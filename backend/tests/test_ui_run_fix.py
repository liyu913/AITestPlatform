from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api.ui_automation import ui_scenario_agent as agent_api
from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.models.ui_automation.ui_automation import UiRun, UiRunStep, UiScenarioAgentSession
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal

_SCRIPT = """async function run(page, agent) {
  await page.goto('/login');
  await expect(page).not.toHaveURL(/\\/(login|signin)\\/?$/i);
}
"""

_FIXED = """async function run(page, agent) {
  await page.goto('/login');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL((url) => !/\\/(login|signin)\\/?$/i.test(url.pathname), { timeout: 15000 });
  await expect(page).not.toHaveURL(/\\/(login|signin)\\/?$/i);
}
"""


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


def _setup_failed_run(token: str, pid: int, *, status: str = "failed") -> tuple[int, int]:
    client = TestClient(app)
    h = {"Authorization": f"Bearer {token}"}
    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "http://localhost:5173", "is_default": True},
    )
    assert env.status_code == 201, env.text
    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={"name": "login", "script": _SCRIPT, "status": "ready"},
    )
    assert cr.status_code == 201, cr.text
    case_id = cr.json()["id"]
    db = TestingSessionLocal()
    try:
        run = UiRun(
            project_id=pid,
            trigger_type="case",
            target_id=case_id,
            env_id=env.json()["id"],
            browser="chromium",
            status=status,
            summary_json={"passed": 0, "failed": 1, "error": 0, "skipped": 0},
        )
        db.add(run)
        db.flush()
        db.add(
            UiRunStep(
                run_id=run.id,
                project_id=pid,
                order=0,
                case_id=case_id,
                case_name="login",
                action="script",
                status="failed" if status == "failed" else "error",
                error_message=(
                    'expect(page).not.toHaveURL(expected) failed\n'
                    'Received string: "http://localhost:5173/login"'
                ),
            )
        )
        db.commit()
        return case_id, run.id
    finally:
        db.close()


def _llm_plan() -> dict:
    return {
        "summary": "登录后仍停在 /login",
        "likely_causes": ["提交后未等待跳转"],
        "changes": ["点击登录后 waitForURL"],
        "warnings": ["若账号错误，修脚本无法通过"],
        "suggested_script": _FIXED,
    }


def _fake_repair_explore(*, status: str = "awaiting_confirm", script: str = _FIXED, error: str = ""):
    def fake_explore(db, session_id: int) -> None:
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        plan = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
        plan["explore_report"] = {"last_url": "http://localhost:5173/"}
        row.plan_json = plan
        row.playwright_code = script
        row.status = status
        row.error_message = error
        get_event_bus().append(row.thread_id, {"type": "log", "node": "explore", "message": "开始重新探索"})
        if status == "awaiting_confirm":
            get_event_bus().append(row.thread_id, {"type": "log", "node": "explore", "message": "探索完成"})
        elif status == "error":
            get_event_bus().append(row.thread_id, {"type": "error", "message": error or "探索失败"})
        db.commit()

    return fake_explore


@contextmanager
def repair_explore(*, status: str = "awaiting_confirm", script: str = _FIXED, error: str = ""):
    agent_api.set_explore_runner(_fake_repair_explore(status=status, script=script, error=error))
    try:
        yield
    finally:
        agent_api.set_explore_runner(None)


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_starts_exploring_then_suggests(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_explore", "p")
    tok = _login("uifix_explore", "p")
    pid = _project(tok, "UiFixExplore")
    case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = _llm_plan()
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    with repair_explore(status="running", script=""):
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "exploring"
    assert body["plan"] is None
    assert not (body.get("plan") or {}).get("suggested_script")
    assert body["explore_session_id"]
    assert body["current_script"] == _SCRIPT
    assert any("重新探索" in str(ev.get("message") or "") for ev in body.get("events") or [])

    sid = body["explore_session_id"]
    db = TestingSessionLocal()
    try:
        row = db.get(UiScenarioAgentSession, sid)
        assert row is not None
        assert row.plan_json.get("_repair_fix_id") == body["id"]
        assert row.plan_json.get("_repair_case_id") == case_id
        row.status = "awaiting_confirm"
        row.playwright_code = _FIXED
        db.commit()
    finally:
        db.close()

    polled = client.get(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix/{body['id']}", headers=h)
    assert polled.status_code == 200, polled.text
    ready = polled.json()
    assert ready["status"] == "awaiting_confirm"
    assert "waitForURL" in (ready["plan"]["suggested_script"] or "")
    assert ready["plan"]["suggested_script"].strip() == _FIXED.strip()

    blocked = client.post(
        f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/confirm",
        headers=h,
        json={"action": "confirm", "name": "should-not-write", "status": "ready"},
    )
    assert blocked.status_code == 400
    assert "不会单独写入新用例" in blocked.text


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_keeps_explore_script_when_llm_drops_locators(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_keep", "p")
    tok = _login("uifix_keep", "p")
    pid = _project(tok, "UiFixKeep")
    _case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = {
        "summary": "编造脚本",
        "likely_causes": ["locator"],
        "changes": ["乱改"],
        "warnings": [],
        "suggested_script": _SCRIPT,
    }
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    with repair_explore():
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "awaiting_confirm"
    assert "getByRole" in (body["plan"]["suggested_script"] or "")
    assert body["plan"]["suggested_script"].strip() == _FIXED.strip()


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_explore_error(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_err", "p")
    tok = _login("uifix_err", "p")
    pid = _project(tok, "UiFixErr")
    _case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = _llm_plan()
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    with repair_explore(status="error", script="", error="页面打不开"):
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.status_code == 201, started.text
    assert started.json()["status"] == "error"
    assert "页面打不开" in started.json()["error_message"]
    assert started.json()["plan"] is None


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_cancel_while_exploring(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_cancel", "p")
    tok = _login("uifix_cancel", "p")
    pid = _project(tok, "UiFixCancel")
    case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = _llm_plan()
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    with repair_explore(status="running", script=""):
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.json()["status"] == "exploring"
    cancelled = client.post(
        f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix/{started.json()['id']}/confirm",
        headers=h,
        json={"action": "cancel"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    saved = client.get(f"/api/v1/projects/{pid}/ui-cases/{case_id}", headers=h)
    assert saved.json()["script"] == _SCRIPT


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_confirm_without_apply(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_skip", "p")
    tok = _login("uifix_skip", "p")
    pid = _project(tok, "UiFixSkip")
    case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = _llm_plan()
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    with repair_explore():
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "awaiting_confirm"
    assert "waitForURL" in (body["plan"]["suggested_script"] or "")

    skipped = client.post(
        f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix/{body['id']}/confirm",
        headers=h,
        json={"action": "confirm", "apply_patch": False},
    )
    assert skipped.status_code == 200, skipped.text
    assert "未修改" in skipped.json()["patch_message"]
    saved = client.get(f"/api/v1/projects/{pid}/ui-cases/{case_id}", headers=h)
    assert saved.status_code == 200
    assert saved.json()["script"] == _SCRIPT


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_confirm_writes_script(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_write", "p")
    tok = _login("uifix_write", "p")
    pid = _project(tok, "UiFixWrite")
    case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = _llm_plan()
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    with repair_explore():
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.status_code == 201, started.text
    applied = client.post(
        f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix/{started.json()['id']}/confirm",
        headers=h,
        json={"action": "confirm", "apply_patch": True, "rerun": False},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["patch_message"].startswith("已写入")
    assert applied.json()["rerun_id"] is None
    saved = client.get(f"/api/v1/projects/{pid}/ui-cases/{case_id}", headers=h)
    assert saved.json()["script"] == _FIXED


@patch("app.services.ui_automation.ui_run_fix.llm_json")
def test_ui_run_fix_confirm_rerun_enqueues(mock_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_rerun", "p")
    tok = _login("uifix_rerun", "p")
    pid = _project(tok, "UiFixRerun")
    case_id, run_id = _setup_failed_run(tok, pid)
    mock_llm.return_value = _llm_plan()
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    queued: list[int] = []

    def fake_apply_async(args=None, countdown=None, queue=None, **kwargs):
        queued.append(args[0])
        m = MagicMock()
        m.id = f"ui-fix-{args[0]}"
        return m

    with repair_explore():
        started = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert started.status_code == 201, started.text
    with patch(
        "app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async",
        side_effect=fake_apply_async,
    ):
        applied = client.post(
            f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix/{started.json()['id']}/confirm",
            headers=h,
            json={"action": "confirm", "apply_patch": True, "rerun": True},
        )
    assert applied.status_code == 200, applied.text
    rerun_id = applied.json()["rerun_id"]
    assert rerun_id is not None
    assert queued == [rerun_id]
    saved = client.get(f"/api/v1/projects/{pid}/ui-cases/{case_id}", headers=h)
    assert saved.json()["script"] == _FIXED
    fresh = client.get(f"/api/v1/projects/{pid}/ui-runs/{rerun_id}", headers=h)
    assert fresh.status_code == 200
    assert fresh.json()["target_id"] == case_id
    assert fresh.json()["status"] == "pending"


def test_ui_run_fix_rejects_passed_run(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "settings_encryption_key", Fernet.generate_key().decode())
    _user("uifix_pass", "p")
    tok = _login("uifix_pass", "p")
    pid = _project(tok, "UiFixPass")
    _case_id, run_id = _setup_failed_run(tok, pid, status="passed")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post(f"/api/v1/projects/{pid}/ui-runs/{run_id}/ai-fix", headers=h, json={})
    assert r.status_code == 400


def test_execute_node_script_maps_expect_failure_and_screenshot():
    from app.services.ui_automation.node_ui_runner import execute_node_ui_script

    shots: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        assert data == b"png-bytes"
        return "1/ui-runs/1/fail_script.png"

    with patch(
        "app.services.ui_automation.node_ui_runner._run_node",
        return_value={
            "ok": False,
            "error": 'expect(page).not.toHaveURL(expected) failed\nReceived string: "http://localhost:5173/login"',
            "status": "failed",
            "screenshotBase64": "cG5nLWJ5dGVz",
            "url": "http://localhost:5173/login",
        },
    ):
        rows = execute_node_ui_script(
            base_url="http://localhost:5173",
            script="async function run(page, agent) {}",
            ctx={},
            screenshot_saver=saver,
        )
    assert rows[0]["status"] == "failed"
    assert "toHaveURL" in rows[0]["error_message"]
    assert rows[0]["screenshot_key"] == "1/ui-runs/1/fail_script.png"
    assert rows[0]["detail_json"]["url"] == "http://localhost:5173/login"
    assert shots == ["fail_script.png"]
    assert isinstance(rows[0].get("duration_ms"), int)


def test_execute_node_script_saves_ok_screenshot_and_forwards_on_log():
    from app.services.ui_automation.node_ui_runner import execute_node_ui_script

    shots: list[str] = []
    logs: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        assert data == b"png-bytes"
        return "1/ui-runs/9/ok_script.png"

    def fake_run_node(script_name, payload, **kwargs):
        assert script_name == "run.mjs"
        on_log = kwargs.get("on_log")
        assert callable(on_log)
        on_log("浏览器已启动 browser=chromium")
        on_log("开始执行脚本")
        on_log("脚本执行完成 url=http://localhost:5173/home")
        return {
            "ok": True,
            "error": "",
            "screenshotBase64": "cG5nLWJ5dGVz",
            "url": "http://localhost:5173/home",
        }

    with patch(
        "app.services.ui_automation.node_ui_runner._run_node",
        side_effect=fake_run_node,
    ):
        rows = execute_node_ui_script(
            base_url="http://localhost:5173",
            script="async function run(page, agent) {}",
            ctx={},
            screenshot_saver=saver,
            on_log=logs.append,
        )
    assert rows[0]["status"] == "passed"
    assert rows[0]["error_message"] == ""
    assert rows[0]["screenshot_key"] == "1/ui-runs/9/ok_script.png"
    assert rows[0]["detail_json"]["url"] == "http://localhost:5173/home"
    assert isinstance(rows[0].get("duration_ms"), int)
    assert shots == ["ok_script.png"]
    assert logs == [
        "浏览器已启动 browser=chromium",
        "开始执行脚本",
        "脚本执行完成 url=http://localhost:5173/home",
    ]


def test_execute_node_script_maps_step_events_to_rows():
    from app.services.ui_automation.node_ui_runner import execute_node_ui_script

    shots: list[str] = []
    emitted: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        return f"1/ui-runs/10/{filename}"

    def fake_run_node(script_name, payload, **kwargs):
        on_step_event = kwargs.get("on_step_event")
        assert callable(on_step_event)
        on_step_event(
            {
                "order": 0,
                "action": "goto",
                "selector": "/",
                "status": "passed",
                "error": "",
                "durationMs": 5,
                "screenshotBase64": "cG5nLWJ5dGVz",
                "url": "http://localhost:5173/",
            }
        )
        on_step_event(
            {
                "order": 1,
                "action": "click",
                "selector": "#go",
                "status": "passed",
                "error": "",
                "durationMs": 3,
                "screenshotBase64": "cG5nLWJ5dGVz",
                "url": "http://localhost:5173/home",
            }
        )
        return {"ok": True, "error": "", "screenshotBase64": "cG5nLWJ5dGVz", "url": "http://localhost:5173/home"}

    with patch(
        "app.services.ui_automation.node_ui_runner._run_node",
        side_effect=fake_run_node,
    ):
        rows = execute_node_ui_script(
            base_url="http://localhost:5173",
            script="async function run(page, agent) {}",
            ctx={},
            screenshot_saver=saver,
            on_step=lambda r: emitted.append(str(r.get("action") or "")),
        )
    assert [r["action"] for r in rows] == ["goto", "click"]
    assert all(r.get("screenshot_key") for r in rows)
    assert shots == ["step_0.png", "step_1.png"]
    assert emitted == ["goto", "click"]


def test_ui_run_executor_appends_script_on_log_to_log_lines():
    """Script-path progress from on_log should land in summary_json.log_lines."""
    from app.services.ui_automation.ui_run_executor import execute_ui_run

    _user("uifix_logs", "p")
    tok = _login("uifix_logs", "p")
    pid = _project(tok, "UiFixLogs")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "http://localhost:5173", "is_default": True},
    )
    assert env.status_code == 201, env.text
    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={"name": "login", "script": _SCRIPT, "status": "ready"},
    )
    assert cr.status_code == 201, cr.text
    case_id = cr.json()["id"]

    def fake_script(**kw):
        on_log = kw.get("on_log")
        assert callable(on_log)
        on_log("浏览器已启动 browser=chromium")
        on_log("开始执行脚本")
        on_log("脚本执行完成 url=http://localhost:5173/home")
        saver = kw.get("screenshot_saver")
        shot = saver(b"png", "ok_script.png") if callable(saver) else None
        row = {
            "action": "script",
            "status": "passed",
            "error_message": "",
            "selector": "",
            "screenshot_key": shot,
            "detail_json": {"url": "http://localhost:5173/home"},
            "duration_ms": 12,
            "retry_count": 0,
        }
        on_step = kw.get("on_step")
        if callable(on_step):
            on_step(row)
        return [row]

    db = TestingSessionLocal()
    try:
        run = UiRun(
            project_id=pid,
            trigger_type="case",
            target_id=case_id,
            env_id=env.json()["id"],
            browser="chromium",
            status="pending",
            summary_json={},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        with patch(
            "app.services.ui_automation.ui_run_executor.execute_ui_script",
            side_effect=fake_script,
        ):
            execute_ui_run(db, run_id)
        db.refresh(run)
        log_lines = list((run.summary_json or {}).get("log_lines") or [])
        assert any("浏览器已启动" in str(x) for x in log_lines)
        assert any("开始执行脚本" in str(x) for x in log_lines)
        assert any("脚本执行完成" in str(x) for x in log_lines)
        assert any("script → passed" in str(x) for x in log_lines)
        steps = (
            db.query(UiRunStep)
            .filter(UiRunStep.run_id == run_id)
            .order_by(UiRunStep.order)
            .all()
        )
        assert len(steps) == 1
        assert steps[0].status == "passed"
        assert steps[0].screenshot_key
    finally:
        db.close()
