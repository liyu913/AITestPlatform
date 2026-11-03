from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.ui_automation.ui_scenario_agent import (
    build_plan_from_prompt,
    credentials_to_ctx,
    extract_credentials_from_text,
    resolve_plan_browser_auth,
)
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


def _project(token: str, name: str = "UISA") -> int:
    r = TestClient(app).post(
        f"/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_build_plan_from_prompt_has_empty_matrix(monkeypatch):
    from app.services.ai.effective_ai_settings import LlmRuntime

    captured: dict = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return (
            '{"goal":"登录后项目管理","start_path":"/login",'
            '"checklist":["登录","项目列表","新建"],"case_name":"项目管理"}'
        )

    monkeypatch.setattr(
        "app.services.ui_automation.ui_scenario_agent.llm.chat_complete",
        fake_chat,
    )
    plan = build_plan_from_prompt(
        prompt="登录后做项目",
        base_url="https://ex.com",
        llm_cfg=LlmRuntime(api_base="https://example.com/v1", api_key="k", model="m", allowed_models_csv=""),
    )
    assert plan["matrix"] == []
    assert plan["case_scripts"] == []
    assert plan["goal"]
    assert len(plan["checklist"]) >= 1
    assert "不要输出 matrix" in captured["messages"][0]["content"]


def test_revise_script_asks_for_python_not_json(monkeypatch):
    captured: dict = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return "def run(page):\n    page.goto('/home')\n"

    monkeypatch.setattr(
        "app.services.ui_automation.ui_scenario_agent.llm.chat_complete",
        fake_chat,
    )
    from app.services.ai.effective_ai_settings import LlmRuntime
    from app.services.ui_automation.ui_scenario_agent import revise_script_with_llm

    code = revise_script_with_llm(
        script="def run(page):\n    page.goto('/')\n",
        instruction="改成打开 /home",
        plan={"goal": "登录"},
        llm_cfg=LlmRuntime(api_base="https://example.com/v1", api_key="k", model="m", allowed_models_csv=""),
    )
    assert code.startswith("def run(page):")
    assert captured["json_mode"] is False
    assert "只输出 Python" in captured["messages"][0]["content"]


def test_extract_credentials_from_text():
    c = extract_credentials_from_text("账号 demo 密码 secret123，登录后打开列表")
    assert c.get("username") == "demo"
    assert c.get("password") == "secret123"
    colon = extract_credentials_from_text("账号：admin；密码：admin123；图片验证码：按页面实际显示正确填写")
    assert colon.get("username") == "admin"
    assert colon.get("password") == "admin123"
    step = extract_credentials_from_text(
        "1. 打开登录页 2. 输入用户名 admin 与密码 admin123 3. 点击登录"
    )
    assert step.get("username") == "admin"
    assert step.get("password") == "admin123"
    ctx = credentials_to_ctx(c)
    assert ctx["LOGIN_USERNAME"] == "demo"
    assert ctx["LOGIN_PASSWORD"] == "secret123"
    # Always string keys even when empty
    empty = credentials_to_ctx({})
    assert empty["username"] == ""
    assert empty["password"] == ""


def test_extract_credentials_combo_账号密码():
    from app.services.ui_automation.ui_scenario_agent import script_env_from_ctx
    from app.services.ui_automation.node_ui_runner import _script_env

    c = extract_credentials_from_text("账号密码：admin admin123\n完成登录 创建项目、需求")
    assert c.get("username") == "admin"
    assert c.get("password") == "admin123"
    c2 = extract_credentials_from_text("账号密码 admin admin123")
    assert c2.get("username") == "admin"
    assert c2.get("password") == "admin123"
    env = script_env_from_ctx(credentials_to_ctx(c))
    assert env["username"] == "admin"
    assert env["password"] == "admin123"
    assert _script_env({}).get("username") == ""
    assert _script_env({}).get("password") == ""
    assert "username" in _script_env({"foo": "bar"})


def test_extract_credentials_rejects_title_outcome_登录成功():
    """Title 「用户名密码登录成功」+ step index must not become username=登录成功."""
    title = "已审批启用用户使用正确用户名密码登录成功"
    steps = "1. 打开登录页\n2. 输入用户名 admin 与密码 admin123\n3. 点击登录"
    # Title alone / title+noise must not yield outcome tokens
    assert extract_credentials_from_text(title) == {}
    assert extract_credentials_from_text(title + "\n1. 打开登录页").get("username") != "登录成功"
    # Steps (or title+steps) yield real creds
    joined = extract_credentials_from_text(title + "\n" + steps)
    assert joined.get("username") == "admin"
    assert joined.get("password") == "admin123"
    # Step-only prose (production scrape excludes title)
    only_steps = extract_credentials_from_text(steps)
    assert only_steps.get("username") == "admin"
    assert only_steps.get("password") == "admin123"


def test_ui_scenario_agent_confirm_writes_case(monkeypatch):
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

    _user("uisa_a", "p")
    tok = _login("uisa_a", "p")
    pid = _project(tok, "UISA1")
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
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        row.status = "awaiting_confirm"
        row.plan_json = {"goal": "打开首页", "start_path": "/", "checklist": [], "case_name": "打开首页"}
        row.trace_json = [
            {"action": "goto", "value": "/", "status": "ok"},
            {"action": "assert_url", "value": "/", "status": "ok"},
            {"action": "done", "status": "ok"},
        ]
        row.result_steps_json = [
            {"action": "goto", "value": "/"},
            {"action": "assert_url", "value": "/"},
        ]
        row.playwright_code = 'def run(page):\n    page.goto("/")\n'
        row.dry_run_status = "passed"
        row.dry_run_error = ""
        msgs = list(row.messages_json) if isinstance(row.messages_json, list) else []
        msgs.append({"role": "assistant", "content": "已生成 2 步。"})
        row.messages_json = msgs
        get_event_bus().append(row.thread_id, {"type": "interrupted", "message": "等待确认"})
        get_event_bus().append(row.thread_id, {"type": "done", "status": "awaiting_confirm"})
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "prompt": "账号 demo 密码 xxx，打开首页",
                "env_id": env_id,
                "browser": "chromium",
            },
        )
        assert cr.status_code == 201, cr.text
        sess = cr.json()
        assert sess["status"] == "awaiting_confirm"
        assert sess["has_credentials"] is True
        assert len(sess["result_steps"]) == 2

        # revise without re-explore: mock LLM revise
        with patch(
            "app.services.ui_automation.ui_scenario_agent.revise_script_with_llm",
            return_value='def run(page):\n    page.goto("/home")\n',
        ):
            mr = client.post(
                f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sess['id']}/messages",
                headers=h,
                json={"content": "起始路径改成 /home"},
            )
        assert mr.status_code == 200, mr.text
        assert 'page.goto("/home")' in mr.json()["playwright_code"]

        conf = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sess['id']}/confirm",
            headers=h,
            json={"action": "confirm", "name": "打开首页 (Agent)", "status": "ready"},
        )
        assert conf.status_code == 200, conf.text
        assert conf.json()["status"] == "confirmed"
        assert conf.json()["saved_ui_case_id"] is not None

        case_id = conf.json()["saved_ui_case_id"]
        gr = client.get(f"/api/v1/projects/{pid}/ui-cases/{case_id}", headers=h)
        assert gr.status_code == 200
        assert gr.json()["name"] == "打开首页 (Agent)"
        assert "def run(page):" in gr.json()["script"]
        assert 'page.goto("/home")' in gr.json()["script"]
        saved_steps = gr.json().get("steps") or []
        assert len(saved_steps) >= 2
        assert saved_steps[0].get("action") == "goto"
        assert saved_steps[1].get("action") == "assert_url"
        # Credentials stay on env / explore session — not copied onto UI case test data
        vars_ = gr.json().get("variables") or {}
        assert vars_ == {}
        assert gr.json().get("auth_mode") is None
    finally:
        agent_api.set_explore_runner(None)


def test_ui_scenario_agent_confirm_persists_auth_mode_none(monkeypatch):
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

    _user("uisa_auth_none", "p")
    tok = _login("uisa_auth_none", "p")
    pid = _project(tok, "UISANONE")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "local",
            "base_url": "https://app.example.com",
            "is_default": True,
            "variables": {"access_token": "tok"},
            "inject_local_storage": {"access_token": "{{access_token}}"},
            "token_inject": "none",
        },
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        prior = row.plan_json if isinstance(row.plan_json, dict) else {}
        assert prior.get("_auth_mode") == "none"
        row.status = "awaiting_confirm"
        row.plan_json = {
            **prior,
            "goal": "登录",
            "start_path": "/login",
            "checklist": [],
            "case_name": "登录",
            "case_scripts": [
                {
                    "id": "UC1",
                    "name": "登录",
                    "ready": True,
                    "code": 'def run(page):\n    page.goto("/login")\n',
                }
            ],
        }
        row.playwright_code = 'def run(page):\n    page.goto("/login")\n'
        row.dry_run_status = "passed"
        get_event_bus().append(row.thread_id, {"type": "done", "status": "awaiting_confirm"})
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "prompt": "登录系统",
                "env_id": env_id,
                "auth_mode": "none",
            },
        )
        assert cr.status_code == 201, cr.text
        sess = cr.json()
        assert sess["auth_mode"] == "none"
        assert sess["status"] == "awaiting_confirm"

        conf = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sess['id']}/confirm",
            headers=h,
            json={"action": "confirm", "name": "登录用例", "status": "ready"},
        )
        assert conf.status_code == 200, conf.text
        case_id = conf.json()["saved_ui_case_id"]
        gr = client.get(f"/api/v1/projects/{pid}/ui-cases/{case_id}", headers=h)
        assert gr.status_code == 200
        assert gr.json()["auth_mode"] == "none"
        assert (gr.json().get("variables") or {}) == {}
    finally:
        agent_api.set_explore_runner(None)


def test_resolve_plan_browser_auth_follow_vs_none():
    class _Env:
        variables_json = {"access_token": "tok-1"}
        secrets_encrypted = None
        inject_headers_json = {}
        inject_local_storage_json = {"access_token": "{{access_token}}"}
        token_inject = "none"
        token_storage_key = None
        accounts_json = []
        auth_states_encrypted = None

    env = _Env()
    follow = resolve_plan_browser_auth(env, {"_auth_mode": None})
    assert follow.local_storage == {"access_token": "tok-1"}
    assert follow.skip_login is True

    none = resolve_plan_browser_auth(env, {"_auth_mode": "none"})
    assert none.mode == "none"
    assert none.local_storage is None
    assert none.skip_login is False


def test_ui_scenario_agent_automate_auth_mode_follow_and_none(monkeypatch):
    """Functional-case automate: follow injects; none skips even when env has inject."""
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

    _user("uisa_auto_auth", "p")
    tok = _login("uisa_auto_auth", "p")
    pid = _project(tok, "UISAAUTH")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "local",
            "base_url": "https://app.example.com",
            "is_default": True,
            "variables": {"access_token": "tok"},
            "inject_local_storage": {"access_token": "{{access_token}}"},
            "token_inject": "none",
        },
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    tcr = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "登录成功",
            "overall_expected": "进入首页",
            "steps": [{"action": "打开登录页", "expected": "看到表单"}],
        },
    )
    assert tcr.status_code == 201, tcr.text
    tc_id = tcr.json()["id"]

    seen: list[str | None] = []

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        prior = row.plan_json if isinstance(row.plan_json, dict) else {}
        seen.append(prior.get("_auth_mode"))
        row.status = "awaiting_confirm"
        row.plan_json = {**prior, "goal": "登录", "case_scripts": []}
        row.playwright_code = 'def run(page):\n    page.goto("/login")\n'
        row.dry_run_status = "passed"
        get_event_bus().append(row.thread_id, {"type": "done", "status": "awaiting_confirm"})
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        follow = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "testcase_id": tc_id,
                "env_id": env_id,
                "execution_mode": "explore_codegen",
            },
        )
        assert follow.status_code == 201, follow.text
        assert follow.json().get("auth_mode") is None

        none = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={
                "testcase_id": tc_id,
                "env_id": env_id,
                "auth_mode": "none",
                "execution_mode": "explore_codegen",
            },
        )
        assert none.status_code == 201, none.text
        assert none.json().get("auth_mode") == "none"
        assert seen == [None, "none"]
    finally:
        agent_api.set_explore_runner(None)


def test_ui_scenario_agent_error_can_continue(monkeypatch):
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

    _user("uisa_err", "p")
    tok = _login("uisa_err", "p")
    pid = _project(tok, "UISAErr")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    calls = {"n": 0}

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        calls["n"] += 1
        if calls["n"] == 1:
            row.status = "error"
            row.error_message = "未安装 ui-runner 依赖"
            msgs = list(row.messages_json) if isinstance(row.messages_json, list) else []
            msgs.append({"role": "assistant", "content": row.error_message})
            row.messages_json = msgs
            get_event_bus().append(row.thread_id, {"type": "error", "message": row.error_message})
            db.commit()
            return
        row.status = "awaiting_confirm"
        row.error_message = ""
        row.plan_json = {"goal": "登录首页", "start_path": "/", "checklist": [], "case_name": "登录"}
        row.result_steps_json = [{"action": "goto", "value": "/"}]
        row.playwright_code = 'def run(page):\n    page.goto("/")\n'
        row.dry_run_status = "passed"
        row.dry_run_error = ""
        msgs = list(row.messages_json) if isinstance(row.messages_json, list) else []
        msgs.append({"role": "assistant", "content": "已生成步骤。"})
        row.messages_json = msgs
        get_event_bus().append(row.thread_id, {"type": "interrupted", "message": "等待确认"})
        get_event_bus().append(row.thread_id, {"type": "done", "status": "awaiting_confirm"})
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={"prompt": "账号 admin 密码 admin123，登录首页", "env_id": env_id},
        )
        assert cr.status_code == 201, cr.text
        sess = cr.json()
        assert sess["status"] == "error"
        sid = sess["id"]

        mr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/messages",
            headers=h,
            json={"content": "装好依赖后再试，验证码 2998"},
        )
        assert mr.status_code == 200, mr.text
        body = mr.json()
        assert body["status"] == "awaiting_confirm"
        assert calls["n"] == 2
        assert "装好依赖后再试" in (body.get("prompt") or "")
        assert "登录首页" in (body.get("prompt") or "")
    finally:
        agent_api.set_explore_runner(None)


def test_ui_scenario_agent_stop_running(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime, MidsceneRuntime
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
    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_midscene_runtime",
        lambda db: MidsceneRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            model_family="",
        ),
    )

    _user("uisa_stop", "p")
    tok = _login("uisa_stop", "p")
    pid = _project(tok, "UISAStop")
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

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        row.status = "running"
        row.celery_task_id = "test-task"
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={"prompt": "打开首页", "env_id": env_id},
        )
        assert cr.status_code == 201, cr.text
        assert cr.json()["status"] == "running"
        sid = cr.json()["id"]

        stopped = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/stop",
            headers=h,
        )
        assert stopped.status_code == 200, stopped.text
        body = stopped.json()
        assert body["status"] == "error"
        assert "已手动停止" in (body.get("error_message") or "")

        from app.services.ui_automation.ui_scenario_agent import execute_session_explore

        db = TestingSessionLocal()
        try:
            execute_session_explore(db, sid)
            from app.models.ui_automation.ui_automation import UiScenarioAgentSession

            row = db.get(UiScenarioAgentSession, sid)
            assert row is not None
            assert row.status == "error"
            assert "已手动停止" in (row.error_message or "")
        finally:
            db.close()
    finally:
        agent_api.set_explore_runner(None)


def test_ui_scenario_agent_awaiting_human_message_and_stop(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime, MidsceneRuntime
    from app.api.ui_automation import ui_scenario_agent as agent_api
    from app.models.ui_automation.ui_automation import UiScenarioAgentSession

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )
    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_midscene_runtime",
        lambda db: MidsceneRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            model_family="",
        ),
    )

    _user("uisa_human", "p")
    tok = _login("uisa_human", "p")
    pid = _project(tok, "UISAHuman")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    explore_calls = {"n": 0}

    def fake_explore(db, session_id: int) -> None:
        explore_calls["n"] += 1
        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        plan = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
        plan["_headed"] = True
        row.plan_json = plan
        row.status = "awaiting_human"
        row.celery_task_id = "human-task"
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={"prompt": "登录", "env_id": env_id, "headed": True},
        )
        assert cr.status_code == 201, cr.text
        assert cr.json()["status"] == "awaiting_human"
        sid = cr.json()["id"]
        assert explore_calls["n"] == 1

        mr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/messages",
            headers=h,
            json={"content": "验证码 8821"},
        )
        assert mr.status_code == 200, mr.text
        body = mr.json()
        assert body["status"] == "awaiting_human"
        # Must not kick off a new explore; worker resumes the same Node process.
        assert explore_calls["n"] == 1

        db = TestingSessionLocal()
        try:
            row = db.get(UiScenarioAgentSession, sid)
            assert row is not None
            plan = row.plan_json if isinstance(row.plan_json, dict) else {}
            assert plan.get("_human_reply") == "验证码 8821"
        finally:
            db.close()

        db = TestingSessionLocal()
        try:
            row = db.get(UiScenarioAgentSession, sid)
            assert row is not None
            plan = dict(row.plan_json) if isinstance(row.plan_json, dict) else {}
            plan.pop("_human_reply", None)
            row.plan_json = plan
            row.status = "awaiting_human"
            db.commit()
        finally:
            db.close()

        stopped = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/stop",
            headers=h,
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "error"
        assert "已手动停止" in (stopped.json().get("error_message") or "")
    finally:
        agent_api.set_explore_runner(None)


def test_ui_scenario_agent_requires_prompt_or_testcase(monkeypatch):
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

    _user("uisa_b", "p")
    tok = _login("uisa_b", "p")
    pid = _project(tok, "UISA2")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201
    bad = client.post(
        f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
        headers=h,
        json={"prompt": "", "env_id": er.json()["id"]},
    )
    assert bad.status_code == 400


def _add_member(pid: int, username: str, role: str = "editor"):
    db = TestingSessionLocal()
    try:
        from app.models.project.project import ProjectMember

        u = db.query(User).filter(User.username == username).one()
        db.add(ProjectMember(project_id=pid, user_id=u.id, role=role))
        db.commit()
    finally:
        db.close()


def test_ui_scenario_agent_session_list_delete_isolation(monkeypatch):
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

    _user("uisa_hist_a", "p")
    _user("uisa_hist_b", "p")
    tok_a = _login("uisa_hist_a", "p")
    tok_b = _login("uisa_hist_b", "p")
    pid = _project(tok_a, "UISAHist")
    _add_member(pid, "uisa_hist_b", "editor")
    client = TestClient(app)
    h_a = {"Authorization": f"Bearer {tok_a}"}
    h_b = {"Authorization": f"Bearer {tok_b}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h_a,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        row.status = "awaiting_confirm"
        row.plan_json = {"goal": "打开首页", "start_path": "/", "checklist": [], "case_name": "打开首页"}
        row.result_steps_json = [{"action": "goto", "value": "/"}]
        row.playwright_code = 'def run(page):\n    page.goto("/")\n'
        row.dry_run_status = "passed"
        msgs = list(row.messages_json) if isinstance(row.messages_json, list) else []
        msgs.append({"role": "assistant", "content": "已生成步骤"})
        row.messages_json = msgs
        get_event_bus().append(row.thread_id, {"type": "done", "status": "awaiting_confirm"})
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h_a,
            json={"prompt": "打开首页并校验列表", "env_id": env_id},
        )
        assert cr.status_code == 201, cr.text
        sid = cr.json()["id"]

        r_list = client.get(f"/api/v1/projects/{pid}/ui-scenario-agent/sessions", headers=h_a)
        assert r_list.status_code == 200
        items = r_list.json()
        assert len(items) == 1
        assert items[0]["id"] == sid
        assert items[0]["title"].startswith("打开首页")
        assert items[0]["status"] == "awaiting_confirm"

        r_other = client.get(f"/api/v1/projects/{pid}/ui-scenario-agent/sessions", headers=h_b)
        assert r_other.status_code == 200
        assert r_other.json() == []

        assert (
            client.get(
                f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}",
                headers=h_b,
            ).status_code
            == 404
        )

        assert (
            client.delete(
                f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}",
                headers=h_a,
            ).status_code
            == 204
        )
        assert client.get(f"/api/v1/projects/{pid}/ui-scenario-agent/sessions", headers=h_a).json() == []
    finally:
        agent_api.set_explore_runner(None)


def test_session_explore_screenshot_get(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    from app.services.ai.effective_ai_settings import LlmRuntime
    from app.api.ui_automation import ui_scenario_agent as agent_api
    from app.services.file_storage import save_ui_explore_screenshot

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("uisa_shot", "p")
    tok = _login("uisa_shot", "p")
    pid = _project(tok, "UISA-SHOT")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]
    saved: dict[str, str] = {}

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.ui_automation.ui_scenario_agent import _emit_commit

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        storage_key, _ = save_ui_explore_screenshot(
            row.project_id, row.id, "000_open.png", b"png-bytes"
        )
        saved["key"] = storage_key
        _emit_commit(
            db,
            row,
            node="explore",
            message="已截图 step=0 action=open",
            data={"screenshot_key": storage_key},
        )
        row.status = "awaiting_confirm"
        row.playwright_code = 'def run(page):\n    page.goto("/")\n'
        db.commit()

    agent_api.set_explore_runner(fake_explore)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={"prompt": "打开首页", "env_id": env_id},
        )
        assert cr.status_code == 201, cr.text
        sid = cr.json()["id"]
        events = cr.json().get("events") or []
        assert any(
            isinstance(ev, dict)
            and (ev.get("data") or {}).get("screenshot_key") == saved["key"]
            for ev in events
        )
        shot = client.get(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/screenshots",
            headers=h,
            params={"key": saved["key"]},
        )
        assert shot.status_code == 200
        assert shot.content == b"png-bytes"
        denied = client.get(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/screenshots",
            headers=h,
            params={"key": f"{pid}/ui-runs/1/x.png"},
        )
        assert denied.status_code == 404
    finally:
        agent_api.set_explore_runner(None)


def test_ui_scenario_agent_trial_run(monkeypatch):
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

    _user("uisa_trial", "p")
    tok = _login("uisa_trial", "p")
    pid = _project(tok, "UISATrial")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://app.example.com", "is_default": True},
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    script = (
        "async function run(page, agent) {\n"
        "  await page.goto('/login');\n"
        "  await expect(page).toHaveURL(/\\/home/);\n"
        "}\n"
    )

    def fake_explore(db, session_id: int) -> None:
        from app.models.ui_automation.ui_automation import UiScenarioAgentSession
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        row.status = "awaiting_confirm"
        row.plan_json = {
            "goal": "登录",
            "start_path": "/login",
            "checklist": ["登录"],
            "case_name": "登录成功",
            "case_scripts": [
                {
                    "id": "TC-01",
                    "name": "登录成功",
                    "ready": True,
                    "code": script,
                }
            ],
            "_headed": True,
        }
        row.playwright_code = script
        row.dry_run_status = None
        row.dry_run_error = ""
        msgs = list(row.messages_json) if isinstance(row.messages_json, list) else []
        msgs.append({"role": "assistant", "content": "已生成脚本。"})
        row.messages_json = msgs
        get_event_bus().append(row.thread_id, {"type": "interrupted", "message": "等待确认"})
        db.commit()

    def fake_trial(db, session_id: int) -> None:
        from app.services.ui_automation.ui_scenario_agent import execute_session_trial_run

        execute_session_trial_run(db, session_id)

    agent_api.set_explore_runner(fake_explore)
    agent_api.set_trial_runner(fake_trial)
    try:
        with patch(
            "app.services.ui_automation.ui_runner.execute_ui_script",
            return_value=[{"action": "goto", "status": "passed", "error_message": ""}],
        ):
            cr = client.post(
                f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
                headers=h,
                json={"prompt": "账号 admin 密码 admin123，登录", "env_id": env_id, "headed": True},
            )
            assert cr.status_code == 201, cr.text
            sess = cr.json()
            assert sess["status"] == "awaiting_confirm"

            tr = client.post(
                f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sess['id']}/trial-run",
                headers=h,
                json={"case_id": "TC-01", "headed": False, "script": script},
            )
            assert tr.status_code == 200, tr.text
            body = tr.json()
            assert body["dry_run_status"] == "passed"
            assert body["dry_run_error"] == ""
            assert body["status"] == "awaiting_confirm"
            events = body.get("events") or []
            assert any(isinstance(ev, dict) and ev.get("type") == "trial_start" for ev in events)
            assert any(
                isinstance(ev, dict)
                and ev.get("type") == "trial_done"
                and ev.get("status") == "passed"
                for ev in events
            )
    finally:
        agent_api.set_explore_runner(None)
        agent_api.set_trial_runner(None)


def test_ui_scenario_agent_trial_run_rejects_while_running(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    from app.services.ai.effective_ai_settings import LlmRuntime
    from app.api.ui_automation import ui_scenario_agent as agent_api
    from app.models.ui_automation.ui_automation import UiScenarioAgentSession

    monkeypatch.setattr(
        "app.api.ui_automation.ui_scenario_agent.get_llm_runtime",
        lambda db: LlmRuntime(
            api_base="https://example.com/v1",
            api_key="test-key",
            model="m",
            allowed_models_csv="m",
        ),
    )

    _user("uisa_trial2", "p")
    tok = _login("uisa_trial2", "p")
    pid = _project(tok, "UISATrial2")
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
        from app.services.api_automation.api_scenario_events import get_event_bus

        row = db.get(UiScenarioAgentSession, session_id)
        assert row is not None
        row.status = "awaiting_confirm"
        row.plan_json = {
            "goal": "x",
            "case_scripts": [
                {
                    "id": "TC-01",
                    "name": "x",
                    "ready": True,
                    "code": "async function run(page, agent) {\n  await page.goto('/');\n}\n",
                }
            ],
        }
        row.playwright_code = "async function run(page, agent) {\n  await page.goto('/');\n}\n"
        get_event_bus().append(row.thread_id, {"type": "interrupted", "message": "等待确认"})
        db.commit()

    def noop_trial(db, session_id: int) -> None:
        # Leave dry_run_status as running (set by enqueue)
        return

    agent_api.set_explore_runner(fake_explore)
    agent_api.set_trial_runner(noop_trial)
    try:
        cr = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions",
            headers=h,
            json={"prompt": "打开", "env_id": env_id},
        )
        assert cr.status_code == 201, cr.text
        sid = cr.json()["id"]
        first = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/trial-run",
            headers=h,
            json={"case_id": "TC-01", "headed": True},
        )
        assert first.status_code == 200, first.text
        assert first.json()["dry_run_status"] == "running"
        second = client.post(
            f"/api/v1/projects/{pid}/ui-scenario-agent/sessions/{sid}/trial-run",
            headers=h,
            json={"case_id": "TC-01", "headed": True},
        )
        assert second.status_code == 400
    finally:
        agent_api.set_explore_runner(None)
        agent_api.set_trial_runner(None)
