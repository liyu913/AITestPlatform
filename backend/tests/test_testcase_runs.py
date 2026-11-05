"""Functional TestCaseRun API + agent_once execution mode."""

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.functional.testcase_run_finalize import judge_agent_once_result
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


def _project(token: str, name: str = "TCR") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_judge_agent_once_result():
    # No trace at all is a hard failure.
    assert judge_agent_once_result([])[0] == "failed"
    # "The agent reached done" is no longer proof of success: without a case
    # with declared expectations the run cannot be judged as passed.
    assert (
        judge_agent_once_result(
            [
                {"action": "goto", "status": "ok"},
                {"action": "done", "status": "ok"},
            ]
        )[0]
        == "blocked"
    )
    # A failing step still fails the run even when the agent ends with done.
    assert (
        judge_agent_once_result(
            [
                {"action": "click", "status": "error", "error": "timeout"},
                {"action": "done", "status": "ok"},
            ]
        )[0]
        == "failed"
    )


def test_snapshot_helpers():
    from app.services.functional.testcase_run_finalize import (
        snapshot_log_lines_from_events,
        snapshot_steps_from_trace,
    )

    steps = snapshot_steps_from_trace(
        [
            {"action": "fill", "selector": "#user", "value": "a", "status": "ok"},
            {"action": "done", "status": "ok"},
        ]
    )
    assert steps[0]["action"] == "fill"
    assert steps[0]["selector"] == "#user"
    logs = snapshot_log_lines_from_events(
        [
            {"message": "开始探索"},
            {"message": "已截图 step=1"},
            {"message": "click → ok"},
        ]
    )
    assert logs == ["开始探索", "click → ok"]


def test_attach_screenshots_to_steps_from_events_and_keys():
    from app.services.functional.testcase_run_finalize import (
        attach_screenshots_to_steps,
        enrich_summary_step_screenshots,
        snapshot_steps_from_trace,
    )

    steps = snapshot_steps_from_trace(
        [
            {"action": "goto", "value": "/login", "status": "ok"},
            {"action": "fill", "selector": "#user", "value": "admin", "status": "ok"},
            {"action": "click", "status": "ok"},
        ]
    )
    assert all(not s.get("screenshot_key") for s in steps)

    k0 = "1/ui-explore/9/000_open.png"
    k1 = "1/ui-explore/9/001_fill.png"
    attach_screenshots_to_steps(
        steps,
        events=[
            {"message": "已截图 step=0 action=open", "data": {"screenshot_key": k0}},
            {"message": "已截图 step=1 action=fill", "data": {"screenshot_key": k1}},
        ],
    )
    assert steps[0]["screenshot_key"] == k0
    assert steps[1]["screenshot_key"] == k1
    assert not steps[2].get("screenshot_key")

    # Filename backfill for old summary_json (no per-step keys)
    bare = snapshot_steps_from_trace(
        [
            {"action": "goto", "status": "ok"},
            {"action": "fill", "status": "ok"},
        ]
    )
    k2 = "1/ui-explore/9/000_open.png"
    k3 = "1/ui-explore/9/001_fill.png"
    enriched = enrich_summary_step_screenshots(
        {"steps": bare, "screenshot_keys": [k2, k3], "step_count": 2}
    )
    assert enriched is not None
    assert enriched["steps"][0]["screenshot_key"] == k2
    assert enriched["steps"][1]["screenshot_key"] == k3
    # original list not mutated with keys from enrich copy path
    assert not bare[0].get("screenshot_key")


def test_agent_once_writes_case_run_and_syncs_status(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime
    from app.api.ui_automation import ui_scenario_agent as agent_api

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("tcr_once", "p")
    tok = _login("tcr_once", "p")
    pid = _project(tok, "TCR1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    tc = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "登录直跑",
            "overall_expected": "进入首页",
            "status": "ready",
            "steps": [{"order": 1, "action": "打开登录页", "expected": "表单可见"}],
        },
    )
    assert tc.status_code == 201, tc.text
    case_id = tc.json()["id"]

    shot_key = f"{pid}/ui-explore/0/step-1.png"

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.ui_automation.ui_scenario_agent import _complete_explore

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        plan = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
        plan.setdefault("goal", "登录")
        plan.setdefault("start_path", "/")
        row.plan_json = plan
        real_shot = f"{pid}/ui-explore/{session_id}/step-1.png"
        row.events_json = [
            {"type": "log", "message": "开始浏览器探索"},
            {"type": "log", "message": "已截图 step=1", "data": {"screenshot_key": real_shot}},
            {"type": "log", "message": "goto → ok"},
        ]
        _complete_explore(
            db,
            row,
            plan=plan,
            trace=[
                {"action": "goto", "value": "/", "status": "ok", "screenshot_key": real_shot},
                {"action": "assert_visible", "assert": "表单可见", "status": "ok"},
                {"action": "assert_text", "assert": "进入首页", "status": "ok"},
                {"action": "done", "status": "ok"},
            ],
            code="should_be_ignored",
            engine="playwright",
            script_kind="node",
        )
        # keep for outer assertions
        nonlocal shot_key
        shot_key = real_shot

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "prompt": "",
                "testcase_id": case_id,
                "env_id": env_id,
                "browser": "chromium",
                "engine": "midscene",
                "execution_mode": "agent_once",
            },
        )
        assert cr.status_code == 201, cr.text
        sess = cr.json()
        assert sess["execution_mode"] == "agent_once"
        assert sess["case_run_id"] is not None
        assert sess["status"] == "done"
        assert (sess.get("playwright_code") or "") == ""

        run_id = sess["case_run_id"]
        gr = client.get(f"/api/v1/projects/{pid}/test-case-runs/{run_id}", headers=h)
        assert gr.status_code == 200, gr.text
        body = gr.json()
        assert body["status"] == "passed"
        assert body["mode"] == "agent_once"
        assert body["case_id"] == case_id
        assert body["session_id"] == sess["id"]
        summary = body.get("summary_json") or {}
        assert summary.get("step_count") == 4
        assert len(summary.get("steps") or []) == 4
        assert summary["steps"][0]["action"] == "goto"
        assert "goto → ok" in (summary.get("log_lines") or [])
        assert "已截图" not in " ".join(summary.get("log_lines") or [])
        assert shot_key in (summary.get("screenshot_keys") or [])
        # Judgement evidence is stored with the run and pinned to a case version.
        judgement = summary.get("judgement") or {}
        assert judgement.get("status") == "passed"
        assert judgement.get("covered_count", 0) >= 2
        assert summary.get("case_version_no") == 1

        # screenshot: unknown key rejected; listed key 404 if file missing
        bad = client.get(
            f"/api/v1/projects/{pid}/test-case-runs/{run_id}/screenshots?key=evil/../x.png",
            headers=h,
        )
        assert bad.status_code == 404
        missing = client.get(
            f"/api/v1/projects/{pid}/test-case-runs/{run_id}/screenshots?key={shot_key}",
            headers=h,
        )
        assert missing.status_code == 404

        case = client.get(f"/api/v1/projects/{pid}/test-cases/{case_id}", headers=h)
        assert case.status_code == 200
        # Execution writes last_result; the legacy status stays the lifecycle.
        assert case.json()["last_result"] == "passed"
        assert case.json()["lifecycle_status"] == "confirmed"
        assert case.json()["status"] == "confirmed"

        # confirm rejected for agent_once
        conf = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sess['id']}/confirm",
            headers=h,
            json={"action": "confirm"},
        )
        assert conf.status_code == 400

        lst = client.get(
            f"/api/v1/projects/{pid}/test-case-runs/?case_id={case_id}",
            headers=h,
        )
        assert lst.status_code == 200
        assert lst.json()["total"] >= 1
        assert any(i["id"] == run_id for i in lst.json()["items"])
    finally:
        agent_api.set_explore_runner(None)


def test_explore_codegen_still_awaits_confirm(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime
    from app.api.ui_automation import ui_scenario_agent as agent_api

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("tcr_code", "p")
    tok = _login("tcr_code", "p")
    pid = _project(tok, "TCR2")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.ui_automation.ui_scenario_agent import _complete_explore

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        plan = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
        plan.setdefault("goal", "打开首页")
        row.plan_json = plan
        _complete_explore(
            db,
            row,
            plan=plan,
            trace=[{"action": "goto", "value": "/", "status": "ok"}, {"action": "done", "status": "ok"}],
            code='def run(page):\n    page.goto("/")\n',
            engine="playwright",
            script_kind="python",
        )

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "prompt": "打开首页",
                "env_id": env_id,
                "execution_mode": "explore_codegen",
            },
        )
        assert cr.status_code == 201, cr.text
        sess = cr.json()
        assert sess["status"] == "awaiting_confirm"
        assert sess["execution_mode"] == "explore_codegen"
        assert sess["case_run_id"] is None
        assert "def run(page)" in (sess.get("playwright_code") or "")

        lst = client.get(f"/api/v1/projects/{pid}/test-case-runs/", headers=h)
        assert lst.status_code == 200
        assert lst.json()["total"] == 0
    finally:
        agent_api.set_explore_runner(None)


def test_agent_once_requires_testcase(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("tcr_req", "p")
    tok = _login("tcr_req", "p")
    pid = _project(tok, "TCR3")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    cr = client.post(
        f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
        headers=h,
        json={
            "prompt": "随便跑一下",
            "env_id": env_id,
            "execution_mode": "agent_once",
        },
    )
    assert cr.status_code == 400


def test_agent_once_cancel_marks_case_run_cancelled(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime
    from app.api.ui_automation import ui_scenario_agent as agent_api

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("tcr_cancel", "p")
    tok = _login("tcr_cancel", "p")
    pid = _project(tok, "TCRCancel")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    tc = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "取消直跑",
            "overall_expected": "可中断",
            "status": "ready",
            "steps": [{"order": 1, "action": "打开页", "expected": "可见"}],
        },
    )
    assert tc.status_code == 201, tc.text
    case_id = tc.json()["id"]

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        row.status = "running"
        row.celery_task_id = "test-task-cancel"
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "prompt": "",
                "testcase_id": case_id,
                "env_id": env_id,
                "browser": "chromium",
                "engine": "midscene",
                "execution_mode": "agent_once",
            },
        )
        assert cr.status_code == 201, cr.text
        sess = cr.json()
        assert sess["status"] == "running"
        run_id = sess["case_run_id"]
        assert run_id is not None

        cancelled = client.post(
            f"/api/v1/projects/{pid}/test-case-runs/{run_id}/cancel",
            headers=h,
        )
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["status"] == "cancelled"
        assert "已手动停止" in (body.get("error_message") or "")

        gr = client.get(f"/api/v1/projects/{pid}/test-case-runs/{run_id}", headers=h)
        assert gr.status_code == 200
        assert gr.json()["status"] == "cancelled"

        again = client.post(
            f"/api/v1/projects/{pid}/test-case-runs/{run_id}/cancel",
            headers=h,
        )
        assert again.status_code == 400
    finally:
        agent_api.set_explore_runner(None)
