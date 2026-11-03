from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.schemas.app_automation.app_automation import AppStepIn
from app.services.identity import auth_service
from app.services.app_automation.app_runner import (
    MissingVariableError,
    execute_app_steps,
    render_template,
    resolve_locator,
)
from app.services.app_automation.app_url_guard import UnsafeAppiumUrlError, assert_safe_appium_url
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


def _project(token: str, name: str = "APP") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_appium_url_guard():
    assert assert_safe_appium_url("http://127.0.0.1:4723") == "http://127.0.0.1:4723"
    assert assert_safe_appium_url("http://192.168.1.10:4723/") == "http://192.168.1.10:4723"
    with pytest.raises(UnsafeAppiumUrlError):
        assert_safe_appium_url("http://evil.example.com:4723")


def test_app_render_template():
    assert render_template("hi {{name}}", {"name": "a"}) == "hi a"
    with pytest.raises(MissingVariableError):
        render_template("{{missing}}", {})


def test_app_resolve_locator():
    els = {"LoginPage.username": {"strategy": "id", "selector": "com.app:id/user"}}
    st, sel = resolve_locator({"element": "LoginPage.username"}, elements=els, ctx={})
    assert st == "id"
    assert sel == "com.app:id/user"
    st2, sel2 = resolve_locator({"selector": "@LoginPage.username"}, elements=els, ctx={})
    assert sel2 == "com.app:id/user"
    st3, sel3 = resolve_locator(
        {"strategy": "xpath", "selector": "//*[@text='{{t}}']"},
        elements=els,
        ctx={"t": "OK"},
    )
    assert st3 == "xpath"
    assert "OK" in sel3


def test_app_step_schema():
    AppStepIn(action="launch")
    AppStepIn(action="tap", selector="com.app:id/btn")
    AppStepIn(action="swipe", value="up")
    with pytest.raises(Exception):
        AppStepIn(action="tap")
    with pytest.raises(Exception):
        AppStepIn(action="type", selector="x")


class _FakeElement:
    def __init__(self, driver: "_FakeDriver", by: str, value: str):
        self.driver = driver
        self.by = by
        self.value = value
        self.id = "el-1"
        self.text = driver.text

    def click(self):
        self.driver.events.append(("click", self.value))

    def clear(self):
        self.driver.events.append(("clear", self.value))

    def send_keys(self, value: str):
        self.driver.events.append(("type", self.value, value))

    def is_displayed(self):
        return True

    def get_attribute(self, name: str):
        if name == "text":
            return self.text
        return ""


class _FakeDriver:
    def __init__(self):
        self.events: list = []
        self.text = "Welcome"
        self._package = "com.demo"

    def find_element(self, by, value):
        self.events.append(("find", by, value))
        return _FakeElement(self, by, value)

    def activate_app(self, package: str):
        self.events.append(("activate", package))

    def back(self):
        self.events.append("back")

    def swipe(self, x1, y1, x2, y2, duration=None):
        self.events.append(("swipe", x1, y1, x2, y2))

    def get_window_size(self):
        return {"width": 1080, "height": 1920}

    def get_screenshot_as_png(self):
        return b"fake-png"

    def hide_keyboard(self):
        self.events.append("hide_keyboard")

    def quit(self):
        self.events.append("quit")


def test_execute_app_steps_with_fake_driver():
    driver = _FakeDriver()
    shots: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        return f"1/app-runs/1/{filename}"

    results = execute_app_steps(
        env={"platform": "android", "app_package": "com.demo", "appium_server_url": "http://127.0.0.1:4723"},
        steps=[
            {"action": "launch"},
            {"action": "type", "selector": "com.demo:id/user", "value": "{{user}}"},
            {"action": "tap", "element": "LoginPage.submit"},
            {"action": "assert_text", "selector": "com.demo:id/title", "assert": "Welcome"},
            {"action": "swipe", "value": "up"},
            {"action": "screenshot"},
        ],
        ctx={"user": "demo"},
        elements={"LoginPage.submit": {"strategy": "id", "selector": "com.demo:id/btn"}},
        screenshot_saver=saver,
        driver_factory=lambda: driver,
    )
    assert all(r["status"] == "passed" for r in results)
    assert ("activate", "com.demo") in driver.events
    assert any(e[0] == "type" and e[2] == "demo" for e in driver.events if isinstance(e, tuple))
    assert shots


def test_env_case_run_project_scoped(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("app_a", "p")
    tok = _login("app_a", "p")
    pid = _project(tok, "AP1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/app-envs/",
        headers=h,
        json={
            "name": "emulator",
            "platform": "android",
            "appium_server_url": "http://127.0.0.1:4723",
            "app_package": "com.demo",
            "app_activity": ".MainActivity",
            "accounts": [{"name": "admin", "variables": {"user": "admin1"}}],
            "variables": {"user": "u1"},
            "secrets": {"password": "secret"},
            "max_concurrency": 2,
            "is_default": True,
        },
    )
    assert er.status_code == 201, er.text
    env = er.json()
    assert "password" in env["secret_keys"]
    assert env["app_package"] == "com.demo"

    bad = client.post(
        f"/api/v1/projects/{pid}/app-envs/",
        headers=h,
        json={
            "name": "evil",
            "platform": "android",
            "appium_server_url": "http://evil.com:4723",
            "app_package": "com.x",
        },
    )
    assert bad.status_code == 422

    el = client.post(
        f"/api/v1/projects/{pid}/app-elements/",
        headers=h,
        json={"page_name": "Home", "name": "title", "strategy": "id", "selector": "com.demo:id/title"},
    )
    assert el.status_code == 201, el.text

    cr = client.post(
        f"/api/v1/projects/{pid}/app-cases/",
        headers=h,
        json={
            "name": "launch smoke",
            "steps": [
                {"action": "launch"},
                {"action": "assert_visible", "element": "Home.title"},
            ],
            "status": "ready",
        },
    )
    assert cr.status_code == 201, cr.text
    case_id = cr.json()["id"]

    executed = []

    def fake_apply_async(args=None, countdown=None, queue=None, **kwargs):
        run_id = args[0]
        from app.services.app_automation.app_run_executor import execute_app_run
        from app.services.app_automation.app_runner import execute_app_steps as real_execute

        driver = _FakeDriver()
        db = TestingSessionLocal()
        try:
            with patch(
                "app.services.app_automation.app_run_executor.execute_app_steps",
                side_effect=lambda **kw: real_execute(**{**kw, "driver_factory": lambda: driver}),
            ):
                execute_app_run(db, run_id)
            executed.append(run_id)
        finally:
            db.close()

        m = MagicMock()
        m.id = f"app-task-{run_id}"
        return m

    with patch("app.tasks.app_automation.run_app_case.run_app_case_task.apply_async", side_effect=fake_apply_async):
        rr = client.post(
            f"/api/v1/projects/{pid}/app-cases/{case_id}/run",
            headers=h,
            json={},
        )
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]
    assert executed == [run_id]

    gr = client.get(f"/api/v1/projects/{pid}/app-runs/{run_id}", headers=h)
    assert gr.status_code == 200
    assert gr.json()["status"] == "passed"

    steps = client.get(f"/api/v1/projects/{pid}/app-runs/{run_id}/steps", headers=h)
    assert steps.status_code == 200
    assert len(steps.json()) == 2
    assert steps.json()[0]["action"] == "launch"

    export = client.get(f"/api/v1/projects/{pid}/app-runs/{run_id}/export?format=junit", headers=h)
    assert export.status_code == 200
    assert b"testcase" in export.content

    trends = client.get(f"/api/v1/projects/{pid}/app-runs/trends?days=7", headers=h)
    assert trends.status_code == 200
    assert trends.json()["totals"]["total"] >= 1

    _user("app_b", "p")
    tok_b = _login("app_b", "p")
    pid_b = _project(tok_b, "AP2")
    h_b = {"Authorization": f"Bearer {tok_b}"}
    cross = client.get(f"/api/v1/projects/{pid_b}/app-cases/{case_id}", headers=h_b)
    assert cross.status_code == 404
    cross_run = client.get(f"/api/v1/projects/{pid_b}/app-runs/{run_id}", headers=h_b)
    assert cross_run.status_code == 404


def test_app_runs_status_filter(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("app_filt", "p")
    tok = _login("app_filt", "p")
    pid = _project(tok, "AppFilt")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    runs = client.get(f"/api/v1/projects/{pid}/app-runs/?status=pending", headers=h)
    assert runs.status_code == 200
    assert "items" in runs.json()
    assert "total" in runs.json()

    envs = client.get(f"/api/v1/projects/{pid}/app-envs/?search=none", headers=h)
    assert envs.status_code == 200
    assert envs.json()["total"] == 0
