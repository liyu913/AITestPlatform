import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.ui_automation.ui_ai_explorer import steps_to_script
from app.services.ui_automation.ui_recorder import (
    normalize_recorded_step,
    recorder_init_script,
    reset_sessions_for_tests,
    suggested_name,
    to_relative_path,
)
from tests.conftest import TestingSessionLocal, requires_integration


@pytest.fixture(autouse=True)
def _reset_recorder_sessions():
    reset_sessions_for_tests()
    yield
    reset_sessions_for_tests()


def _user(username: str, pw: str) -> None:
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
        json={"name": "rec"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_normalize_and_relative_path():
    assert normalize_recorded_step({"action": "click", "selector": "#go"}) == {
        "action": "click",
        "selector": "#go",
    }
    assert normalize_recorded_step({"action": "goto", "value": "https://evil.com"}) is None
    assert normalize_recorded_step({"action": "goto", "value": "/login"}) == {
        "action": "goto",
        "value": "/login",
    }
    assert to_relative_path("https://app.example.com/app", "https://app.example.com/app/login?x=1") == "/login?x=1"
    assert to_relative_path("https://app.example.com", "https://other.example.com/login") is None
    assert suggested_name("  登录  ") == "登录"
    assert suggested_name("").startswith("录制 ")


def test_record_then_create_case(monkeypatch):
    def fake(session):
        session.add_step({"action": "goto", "value": "/"})
        session.add_step({"action": "click", "selector": "#login"})
        session.add_step({"action": "goto", "value": "https://evil.com"})
        session.suggested_name = "登录页"
        session.mark_ready()
        session.stop_event.wait(10)

    monkeypatch.setattr("app.services.ui_automation.ui_recorder.run_recording", fake)

    name = "rec_" + uuid.uuid4().hex[:8]
    _user(name, "p")
    tok = _login(name, "p")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert env.status_code == 201, env.text

    started = client.post(f"/api/v1/projects/{pid}/ui-recordings/", headers=h, json={})
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["status"] == "recording"
    assert body["base_url"] == "https://app.example.com"

    busy = client.post(f"/api/v1/projects/{pid}/ui-recordings/", headers=h, json={})
    assert busy.status_code == 409

    viewing = client.get(f"/api/v1/projects/{pid}/ui-recordings/{body['id']}", headers=h)
    assert viewing.status_code == 200
    assert viewing.json()["step_count"] == 2

    stopped = client.post(f"/api/v1/projects/{pid}/ui-recordings/{body['id']}/stop", headers=h)
    assert stopped.status_code == 200, stopped.text
    done = stopped.json()
    assert done["status"] == "stopped"
    assert done["suggested_name"] == "登录页"
    assert 'page.goto("/")' in done["script"]
    assert 'page.locator("#login").click()' in done["script"]
    assert "evil.com" not in done["script"]

    created = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={"name": done["suggested_name"], "script": done["script"], "status": "draft"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "登录页"
    assert "def run(page):" in created.json()["script"]


_LOGIN_HTML = """
<!DOCTYPE html>
<html>
<body>
  <div class="card">
    <h1>登录</h1>
    <form>
      <label>账号<input type="text" /></label>
      <label>密码<input type="password" /></label>
      <label>验证码<input type="text" placeholder="请输入图中字符" /></label>
      <label><input type="checkbox" />记住密码</label>
      <button type="button">登录</button>
    </form>
    <input placeholder="搜索项目" />
    <input id="title" />
    <button type="button" data-testid="create">保存</button>
    <div><div><a href="#proj">演示项目</a></div></div>
    <div><button type="button"><span>新建需求</span></button></div>
    <div role="listbox"><div role="option"><div>高</div></div></div>
  </div>
</body>
</html>
"""


@requires_integration
def test_recorder_prefers_stable_selectors():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    steps: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.expose_function("rainaRecordStep", lambda payload: steps.append(payload))
            context.add_init_script(recorder_init_script("http://127.0.0.1:9"))
            context.route(
                "http://127.0.0.1:9/**",
                lambda route: route.fulfill(body=_LOGIN_HTML, content_type="text/html; charset=utf-8"),
            )
            page = context.new_page()
            page.goto("http://127.0.0.1:9/login", wait_until="domcontentloaded")
            fields = page.locator("form label")
            fields.nth(0).locator("input").click()
            fields.nth(1).locator("input").click()
            fields.nth(2).locator("input").click()
            page.locator('input[placeholder="搜索项目"]').click()
            page.locator("#title").click()
            page.get_by_test_id("create").click()
            page.get_by_role("checkbox", name="记住密码").click()
            page.get_by_role("button", name="登录", exact=True).click()
            page.get_by_role("link", name="演示项目").click()
            page.locator("button", has_text="新建需求").locator("span").click()
            page.get_by_role("option", name="高").locator("div").click()
        finally:
            browser.close()

    selectors = [step.get("selector", "") for step in steps if isinstance(step, dict)]
    assert "label=账号" in selectors
    assert "label=密码" in selectors
    assert "label=验证码" in selectors
    assert "placeholder=搜索项目" in selectors
    assert "#title" in selectors
    assert "testid=create" in selectors
    assert 'role=checkbox[name="记住密码"]' in selectors
    assert 'role=button[name="登录"]' in selectors
    assert 'role=link[name="演示项目"]' in selectors
    assert 'role=button[name="新建需求"]' in selectors
    assert 'role=option[name="高"]' in selectors
    assert all("nth-of-type" not in sel for sel in selectors)

    script = steps_to_script([step for step in steps if step.get("action") == "click"])
    assert 'page.get_by_label("账号", exact=True).click()' in script
    assert 'page.get_by_role("button", name="登录", exact=True).click()' in script
    assert 'page.get_by_role("link", name="演示项目", exact=True).click()' in script
    assert "nth-of-type" not in script


def test_record_requires_base_url():
    name = "rec_" + uuid.uuid4().hex[:8]
    _user(name, "p")
    tok = _login(name, "p")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/ui-recordings/",
        headers={"Authorization": f"Bearer {tok}"},
        json={},
    )
    assert r.status_code == 400
    assert "base_url" in r.json()["message"]
