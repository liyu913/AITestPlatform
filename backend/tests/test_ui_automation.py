from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.schemas.ui_automation.ui_automation import UiStepIn
from app.services.identity import auth_service
from app.services.ui_automation.ui_runner import (
    MissingVariableError,
    execute_ui_script,
    execute_ui_steps,
    render_template,
)
from app.services.ui_automation.ui_url_guard import UnsafeUiUrlError, join_base_and_path
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


def _project(token: str, name: str = "UI") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_ui_join_base_and_path_ok():
    assert join_base_and_path("https://example.com/app", "/login") == "https://example.com/app/login"
    assert join_base_and_path("https://example.com", "/login?x=1") == "https://example.com/login?x=1"


def test_ui_join_rejects_absolute():
    with pytest.raises(UnsafeUiUrlError):
        join_base_and_path("https://example.com", "https://evil.com/x")


def test_ui_render_template():
    assert render_template("hi {{name}}", {"name": "a"}) == "hi a"
    with pytest.raises(MissingVariableError):
        render_template("{{missing}}", {})


def test_ui_resolve_element():
    from app.services.ui_automation.ui_runner import resolve_selector

    els = {"LoginPage.username": "#user"}
    assert resolve_selector({"element": "LoginPage.username"}, elements=els, ctx={}) == "#user"
    assert resolve_selector({"selector": "@LoginPage.username"}, elements=els, ctx={}) == "#user"
    assert resolve_selector({"selector": "#plain"}, elements=els, ctx={}) == "#plain"


def test_ui_step_schema_goto():
    UiStepIn(action="goto", value="/login")
    UiStepIn(action="wait_for", assert_="networkidle")
    with pytest.raises(Exception):
        UiStepIn(action="goto", value="https://evil.com")
    with pytest.raises(Exception):
        UiStepIn(action="click")


class _FakeLocator:
    def __init__(self, page: "_FakePage", *, via: str = "locator", detail: str = ""):
        self.page = page
        self.via = via
        self.detail = detail

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.events.append(("click", self.via, self.detail))

    def fill(self, value, timeout=None):
        self.page.events.append(("fill", value, self.via, self.detail))

    def select_option(self, value, timeout=None):
        self.page.events.append(("select", value))

    def press(self, key, timeout=None):
        self.page.events.append(("press", key))

    def hover(self, timeout=None):
        self.page.events.append("hover")

    def wait_for(self, state=None, timeout=None):
        self.page.events.append(("wait", state, self.via, self.detail))

    def inner_text(self, timeout=None):
        return self.page.text


class _FakePage:
    def __init__(self):
        self.url = "https://example.com/"
        self.events: list = []
        self.text = "Welcome"

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.events.append(("goto", url))

    def wait_for_load_state(self, state=None, timeout=None):
        self.events.append(("load_state", state))

    def locator(self, sel):
        if "getByRole" in str(sel) or "get_by_role" in str(sel):
            raise AssertionError(f"getByRole must not be passed to locator(): {sel!r}")
        return _FakeLocator(self, via="locator", detail=str(sel))

    def get_by_role(self, role, **kwargs):
        name = kwargs.get("name")
        return _FakeLocator(self, via="get_by_role", detail=f"{role}:{name}")

    def get_by_placeholder(self, text, **kwargs):
        return _FakeLocator(self, via="get_by_placeholder", detail=str(text))

    def get_by_text(self, text, **kwargs):
        return _FakeLocator(self, via="get_by_text", detail=str(text))

    def get_by_label(self, text, **kwargs):
        return _FakeLocator(self, via="get_by_label", detail=str(text))

    def get_by_test_id(self, tid, **kwargs):
        return _FakeLocator(self, via="get_by_test_id", detail=str(tid))

    def get_by_title(self, text, **kwargs):
        return _FakeLocator(self, via="get_by_title", detail=str(text))

    def get_by_alt_text(self, text, **kwargs):
        return _FakeLocator(self, via="get_by_alt_text", detail=str(text))

    def wait_for_url(self, pattern, timeout=None):
        self.events.append(("wait_url", pattern))

    def screenshot(self, full_page=False):
        return b"fake-png"


def test_execute_ui_steps_with_fake_page():
    page = _FakePage()
    shots: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        return f"1/ui-runs/1/{filename}"

    results = execute_ui_steps(
        base_url="https://example.com",
        steps=[
            {"action": "goto", "value": "/login"},
            {"action": "fill", "selector": "#user", "value": "{{user}}"},
            {"action": "click", "element": "LoginPage.submit"},
            {"action": "assert_text", "selector": "h1", "assert": "Welcome"},
            {"action": "wait_for", "assert": "networkidle"},
            {"action": "screenshot"},
        ],
        ctx={"user": "demo"},
        elements={"LoginPage.submit": "button"},
        screenshot_saver=saver,
        browser_factory=lambda: page,
    )
    assert all(r["status"] == "passed" for r in results)
    assert ("goto", "https://example.com/login") in page.events
    assert ("fill", "demo", "locator", "#user") in page.events
    assert ("load_state", "networkidle") in page.events
    assert len(shots) == len(results)
    assert all(r.get("screenshot_key") for r in results)

    seen: list[str] = []

    def on_step(row: dict) -> None:
        seen.append(str(row.get("action") or ""))

    execute_ui_steps(
        base_url="https://example.com",
        steps=[{"action": "goto", "value": "/"}],
        ctx={},
        screenshot_saver=saver,
        browser_factory=lambda: _FakePage(),
        on_step=on_step,
    )
    assert seen == ["goto"]


def test_sanitize_accessible_name_strips_icon_prefix():
    from app.services.ui_automation.ui_runner import rewrite_getby_name, sanitize_accessible_name

    assert sanitize_accessible_name("file-text 需求管理") == "需求管理"
    assert sanitize_accessible_name("settings 设置") == "设置"
    assert sanitize_accessible_name("user name") == "user name"
    assert "file-text" not in rewrite_getby_name(
        "getByRole('link', { name: 'file-text 需求管理' })"
    )


def test_execute_ui_steps_getbyrole_not_css():
    """Regression: DSL steps with getByRole must call get_by_role, not page.locator(CSS)."""
    from app.services.ui_automation.ui_runner import resolve_page_locator

    page = _FakePage()
    loc = resolve_page_locator(page, "getByRole('link', { name: 'file-text 需求管理' })")
    assert loc.via == "get_by_role"
    assert loc.detail == "link:需求管理"

    loc2 = resolve_page_locator(page, 'page.getByRole("button", { name: /创建|确定/ })')
    assert loc2.via == "get_by_role"
    assert "创建" in str(loc2.detail)

    results = execute_ui_steps(
        base_url="https://example.com",
        steps=[
            {
                "action": "click",
                "selector": "getByRole('link', { name: 'file-text 需求管理' })",
            }
        ],
        ctx={},
        browser_factory=lambda: page,
        screenshot_saver=None,
        screenshot_each_step=False,
    )
    assert results[0]["status"] == "passed"
    assert any(e[0] == "click" and e[1] == "get_by_role" for e in page.events if isinstance(e, tuple))


def test_execute_ui_script_with_fake_page():
    page = _FakePage()
    results = execute_ui_script(
        base_url="https://example.com",
        script='def run(page):\n    page.goto("/login")\n    page.locator("#user").fill(env["user"])\n',
        ctx={"user": "demo"},
        browser_factory=lambda: page,
    )
    assert [r["action"] for r in results] == ["goto", "fill"]
    assert all(r["status"] == "passed" for r in results)
    assert ("goto", "https://example.com/login") in page.events
    assert ("fill", "demo", "locator", "#user") in page.events

    missing = execute_ui_script(
        base_url="https://example.com",
        script="x = 1\n",
        ctx={},
        browser_factory=lambda: _FakePage(),
    )
    assert missing[0]["status"] == "error"
    assert missing[0]["action"] == "script"
    assert "def run" in missing[0]["error_message"]


def test_execute_ui_script_saves_ok_screenshot_before_close():
    """Success shot must run while the page is still open (not after browser.close)."""

    class _ClosingPage(_FakePage):
        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

        def screenshot(self, full_page=False):
            if self.closed:
                raise RuntimeError("page already closed")
            return b"ok-png"

    page = _ClosingPage()
    shots: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        return f"1/ui-runs/1/{filename}"

    results = execute_ui_script(
        base_url="https://example.com",
        script="def run(page):\n    page.goto('/')\n",
        ctx={},
        browser_factory=lambda: page,
        screenshot_saver=saver,
    )
    assert results[0]["status"] == "passed"
    assert results[0]["action"] == "goto"
    assert results[0]["screenshot_key"] == "1/ui-runs/1/step_0.png"
    assert shots == ["step_0.png"]

    # Simulate Playwright path timing: close then try shot → must already have key from before close.
    page.close()
    assert page.closed
    late = None
    try:
        late = page.screenshot()
    except RuntimeError:
        late = None
    assert late is None


def test_execute_ui_script_saves_fail_screenshot():
    page = _FakePage()
    shots: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        return f"1/ui-runs/2/{filename}"

    results = execute_ui_script(
        base_url="https://example.com",
        script='def run(page):\n    raise AssertionError("boom")\n',
        ctx={},
        browser_factory=lambda: page,
        screenshot_saver=saver,
    )
    assert results[0]["status"] == "failed"
    assert results[0]["screenshot_key"] == "1/ui-runs/2/fail_script.png"
    assert shots == ["fail_script.png"]


def test_execute_ui_script_per_step_screenshots():
    page = _FakePage()
    shots: list[str] = []
    emitted: list[str] = []

    def saver(data: bytes, filename: str) -> str:
        shots.append(filename)
        return f"1/ui-runs/3/{filename}"

    def on_step(row: dict) -> None:
        emitted.append(str(row.get("action") or ""))

    results = execute_ui_script(
        base_url="https://example.com",
        script=(
            "def run(page):\n"
            "    page.goto('/login')\n"
            "    page.locator('#user').fill(env['user'])\n"
            "    page.locator('#submit').click()\n"
        ),
        ctx={"user": "demo"},
        browser_factory=lambda: page,
        screenshot_saver=saver,
        on_step=on_step,
    )
    assert [r["action"] for r in results] == ["goto", "fill", "click"]
    assert all(r["status"] == "passed" for r in results)
    assert all(r.get("screenshot_key") for r in results)
    assert shots == ["step_0.png", "step_1.png", "step_2.png"]
    assert emitted == ["goto", "fill", "click"]


def test_env_case_run_project_scoped(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("ui_a", "p")
    tok = _login("ui_a", "p")
    pid = _project(tok, "UP1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "local",
            "base_url": "https://app.example.com",
            "browsers": ["chromium"],
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
    assert env["max_concurrency"] == 2
    assert env["accounts"][0]["name"] == "admin"

    el = client.post(
        f"/api/v1/projects/{pid}/ui-elements/",
        headers=h,
        json={"page_name": "Home", "name": "title", "selector": "h1"},
    )
    assert el.status_code == 201, el.text

    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "login smoke",
            "steps": [
                {"action": "goto", "value": "/"},
                {"action": "fill", "selector": "#user", "value": "{{user}}"},
            ],
            "status": "ready",
        },
    )
    assert cr.status_code == 201, cr.text
    assert "def run(page):" in cr.json()["script"]
    assert 'page.goto("/")' in cr.json()["script"]
    assert 'env["user"]' in cr.json()["script"]
    assert len(cr.json().get("steps") or []) == 2
    case_id = cr.json()["id"]
    patched = client.patch(
        f"/api/v1/projects/{pid}/ui-cases/{case_id}",
        headers=h,
        json={"script": 'def run(page):\n    page.goto("/")\n'},
    )
    assert patched.status_code == 200, patched.text
    assert not (patched.json().get("steps") or [])

    suite = client.post(
        f"/api/v1/projects/{pid}/ui-suites/",
        headers=h,
        json={
            "name": "smoke suite",
            "case_ids": [case_id],
            "browsers": ["chromium"],
            "stop_on_fail": True,
        },
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]

    executed = []

    def fake_apply_async(args=None, countdown=None, queue=None, **kwargs):
        run_id = args[0]
        from app.services.ui_automation.ui_run_executor import execute_ui_run

        page = _FakePage()
        page.url = "https://app.example.com/"

        db = TestingSessionLocal()
        try:
            with patch(
                "app.services.ui_automation.ui_run_executor.execute_ui_script",
                side_effect=lambda **kw: execute_ui_script(
                    **{**kw, "browser_factory": lambda: page}
                ),
            ):
                execute_ui_run(db, run_id)
            executed.append(run_id)
        finally:
            db.close()

        m = MagicMock()
        m.id = f"ui-task-{run_id}"
        return m

    with patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", side_effect=fake_apply_async):
        rr = client.post(
            f"/api/v1/projects/{pid}/ui-cases/{case_id}/run",
            headers=h,
            json={},
        )
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]
    assert executed == [run_id]

    gr = client.get(f"/api/v1/projects/{pid}/ui-runs/{run_id}", headers=h)
    assert gr.status_code == 200
    assert gr.json()["status"] == "passed"
    log_lines = (gr.json().get("summary_json") or {}).get("log_lines") or []
    assert any("开始执行" in str(x) for x in log_lines)
    assert any("script →" in str(x) for x in log_lines)

    steps = client.get(f"/api/v1/projects/{pid}/ui-runs/{run_id}/steps", headers=h)
    assert steps.status_code == 200
    assert len(steps.json()) == 1
    assert steps.json()[0]["action"] == "goto"
    assert steps.json()[0]["status"] == "passed"
    assert steps.json()[0].get("screenshot_key")

    export = client.get(f"/api/v1/projects/{pid}/ui-runs/{run_id}/export?format=junit", headers=h)
    assert export.status_code == 200
    assert b"testcase" in export.content

    trends = client.get(f"/api/v1/projects/{pid}/ui-runs/trends?days=7", headers=h)
    assert trends.status_code == 200
    assert trends.json()["totals"]["total"] >= 1

    with patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", side_effect=fake_apply_async):
        sr = client.post(
            f"/api/v1/projects/{pid}/ui-suites/{suite_id}/run",
            headers=h,
            json={"sync": False},
        )
    assert sr.status_code == 201, sr.text
    assert isinstance(sr.json(), list)
    assert len(sr.json()) >= 1

    ci = client.post(
        f"/api/v1/projects/{pid}/ui-runs/ci-trigger",
        headers=h,
        json={"suite_id": suite_id},
    )
    # may enqueue without worker - just check API accepts
    assert ci.status_code in (201, 200), ci.text

    sch = client.post(
        f"/api/v1/projects/{pid}/ui-schedules/",
        headers=h,
        json={
            "name": "hourly",
            "suite_id": suite_id,
            "cron_expr": "0 * * * *",
            "enabled": True,
        },
    )
    assert sch.status_code == 201, sch.text

    _user("ui_b", "p")
    tok_b = _login("ui_b", "p")
    pid_b = _project(tok_b, "UP2")
    h_b = {"Authorization": f"Bearer {tok_b}"}
    cross = client.get(f"/api/v1/projects/{pid_b}/ui-cases/{case_id}", headers=h_b)
    assert cross.status_code == 404
    cross_run = client.get(f"/api/v1/projects/{pid_b}/ui-runs/{run_id}", headers=h_b)
    assert cross_run.status_code == 404


def test_ui_list_search_filters(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("ui_filt", "p")
    tok = _login("ui_filt", "p")
    pid = _project(tok, "UiFilt")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "UiAlpha",
            "base_url": "https://example.com",
            "browsers": ["chromium"],
            "is_default": True,
        },
    )
    assert env.status_code == 201, env.text
    client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "UiBeta", "base_url": "https://example.com", "browsers": ["chromium"]},
    )
    es = client.get(f"/api/v1/projects/{pid}/ui-envs/?search=Alpha", headers=h)
    assert es.status_code == 200
    assert es.json()["total"] == 1

    suite = client.post(
        f"/api/v1/projects/{pid}/ui-suites/",
        headers=h,
        json={"name": "UiSmoke", "case_ids": [], "stop_on_fail": True, "browsers": ["chromium"]},
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]
    ss = client.get(f"/api/v1/projects/{pid}/ui-suites/?search=Smoke", headers=h)
    assert ss.status_code == 200
    assert ss.json()["total"] == 1

    sch = client.post(
        f"/api/v1/projects/{pid}/ui-schedules/",
        headers=h,
        json={"name": "UiHourly", "suite_id": suite_id, "cron_expr": "0 * * * *", "enabled": True},
    )
    assert sch.status_code == 201, sch.text
    sc = client.get(f"/api/v1/projects/{pid}/ui-schedules/?search=Hourly", headers=h)
    assert sc.status_code == 200
    assert sc.json()["total"] == 1


def test_ui_run_headless_flag(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("ui_head", "p")
    tok = _login("ui_head", "p")
    pid = _project(tok, "UiHead")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={"name": "open", "steps": [{"action": "goto", "value": "/"}], "status": "ready"},
    )
    assert cr.status_code == 201, cr.text
    case_id = cr.json()["id"]
    suite = client.post(
        f"/api/v1/projects/{pid}/ui-suites/",
        headers=h,
        json={"name": "pack", "case_ids": [case_id], "browsers": ["chromium"]},
    )
    assert suite.status_code == 201, suite.text
    suite_id = suite.json()["id"]

    captured: list[bool] = []

    def fake_runner(**kw):
        captured.append(bool(kw.get("headless", True)))
        on_step = kw.get("on_step")
        rows = [
            {
                "status": "passed",
                "action": "goto",
                "error_message": "",
                "duration_ms": 1,
                "selector": "",
                "screenshot_key": "1/ui-runs/1/step_0.png",
                "detail_json": {},
                "retry_count": 0,
            }
        ]
        if callable(on_step):
            for row in rows:
                on_step(row)
        return rows

    def fake_apply_async(args=None, countdown=None, queue=None, **kwargs):
        run_id = args[0]
        from app.services.ui_automation.ui_run_executor import execute_ui_run

        db = TestingSessionLocal()
        try:
            with (
                patch(
                    "app.services.ui_automation.ui_run_executor.execute_ui_script",
                    side_effect=fake_runner,
                ),
                patch(
                    "app.services.ui_automation.ui_run_executor.execute_ui_steps",
                    side_effect=fake_runner,
                ),
            ):
                execute_ui_run(db, run_id)
        finally:
            db.close()
        m = MagicMock()
        m.id = f"ui-task-{run_id}"
        return m

    with patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", side_effect=fake_apply_async):
        default_run = client.post(f"/api/v1/projects/{pid}/ui-cases/{case_id}/run", headers=h, json={})
    assert default_run.status_code == 201, default_run.text
    assert default_run.json()["headless"] is True
    assert captured == [True]

    captured.clear()
    with patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", side_effect=fake_apply_async):
        headed_run = client.post(
            f"/api/v1/projects/{pid}/ui-cases/{case_id}/run",
            headers=h,
            json={"headless": False},
        )
    assert headed_run.status_code == 201, headed_run.text
    assert headed_run.json()["headless"] is False
    assert captured == [False]

    captured.clear()
    with patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", side_effect=fake_apply_async):
        suite_run = client.post(
            f"/api/v1/projects/{pid}/ui-suites/{suite_id}/run",
            headers=h,
            json={"headless": False},
        )
    assert suite_run.status_code == 201, suite_run.text
    assert suite_run.json()[0]["headless"] is False
    assert captured == [False]


def test_dsl_steps_prefers_runnable_script():
    """Agent cases have both script and steps_json; script must win (avoids about:blank)."""
    from app.models.ui_automation.ui_automation import UiCase
    from app.services.ui_automation.ui_run_executor import _dsl_steps

    both = UiCase(
        project_id=1,
        name="agent case",
        script=(
            "async function run(page, agent) {\n"
            "  await page.goto('/');\n"
            "}"
        ),
        steps_json=[
            {"action": "click", "selector": "getByRole('link', { name: '需求管理' })"},
        ],
    )
    assert _dsl_steps(both) is None

    steps_only = UiCase(
        project_id=1,
        name="legacy steps",
        script="",
        steps_json=[
            {"action": "goto", "value": "/"},
            {"action": "click", "selector": "button"},
        ],
    )
    assert _dsl_steps(steps_only) is not None
    assert len(_dsl_steps(steps_only)) == 2

    midscene = UiCase(
        project_id=1,
        name="mixed",
        script="",
        steps_json=[
            {"action": "click", "driver": "midscene", "locate": "按钮"},
        ],
    )
    assert _dsl_steps(midscene) is None


def test_ui_run_steps_json_persists_incrementally(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("ui_steps", "p")
    tok = _login("ui_steps", "p")
    pid = _project(tok, "UiSteps")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    cr = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "multi step",
            "steps": [
                {"action": "goto", "value": "/"},
                {"action": "click", "selector": "button"},
            ],
            "status": "ready",
        },
    )
    assert cr.status_code == 201, cr.text
    case_id = cr.json()["id"]
    assert len(cr.json().get("steps") or []) == 2
    # Create-from-steps also compiles a script; clear it so this test covers DSL path.
    from app.models.ui_automation.ui_automation import UiCase

    db = TestingSessionLocal()
    try:
        row = db.get(UiCase, case_id)
        assert row is not None
        row.script = ""
        db.commit()
    finally:
        db.close()

    def fake_steps(**kw):
        on_step = kw.get("on_step")
        saver = kw.get("screenshot_saver")
        rows = []
        for i, action in enumerate(("goto", "click")):
            key_shot = saver(b"png", f"step_{i}.png") if callable(saver) else f"shot_{i}"
            row = {
                "order": i,
                "action": action,
                "selector": "" if action == "goto" else "button",
                "status": "passed",
                "error_message": "",
                "duration_ms": 5 + i,
                "screenshot_key": key_shot,
                "detail_json": {},
                "retry_count": 0,
            }
            rows.append(row)
            if callable(on_step):
                on_step(row)
        return rows

    def fake_apply_async(args=None, countdown=None, queue=None, **kwargs):
        run_id = args[0]
        from app.services.ui_automation.ui_run_executor import execute_ui_run

        db = TestingSessionLocal()
        try:
            with patch(
                "app.services.ui_automation.ui_run_executor.execute_ui_steps",
                side_effect=fake_steps,
            ):
                execute_ui_run(db, run_id)
        finally:
            db.close()
        m = MagicMock()
        m.id = f"ui-task-{run_id}"
        return m

    with patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", side_effect=fake_apply_async):
        rr = client.post(f"/api/v1/projects/{pid}/ui-cases/{case_id}/run", headers=h, json={})
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]

    gr = client.get(f"/api/v1/projects/{pid}/ui-runs/{run_id}", headers=h)
    assert gr.status_code == 200
    assert gr.json()["status"] == "passed"
    logs = (gr.json().get("summary_json") or {}).get("log_lines") or []
    assert any("goto → passed" in str(x) for x in logs)
    assert any("click → passed" in str(x) for x in logs)

    steps = client.get(f"/api/v1/projects/{pid}/ui-runs/{run_id}/steps", headers=h)
    assert steps.status_code == 200
    body = steps.json()
    assert len(body) == 2
    assert body[0]["action"] == "goto"
    assert body[1]["action"] == "click"
    assert body[0]["screenshot_key"]
    assert body[1]["screenshot_key"]
