from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.ui_automation.ui_ai_explorer import (
    build_plan_from_testcase,
    selector_to_dsl,
    steps_to_script,
    trace_to_playwright_code,
    trace_to_ui_steps,
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


def _project(token: str, name: str = "UIAI") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_trace_to_ui_steps_and_playwright_code():
    trace = [
        {"action": "goto", "value": "/login", "status": "ok"},
        {"action": "fill", "selector": "#user", "value": "demo", "status": "ok"},
        {"action": "click", "selector": 'role=button[name="登录"]', "status": "ok"},
        {"action": "assert_url", "value": "/home", "status": "ok"},
        {"action": "done", "status": "ok"},
        {"action": "click", "selector": "#x", "status": "error", "error": "boom"},
    ]
    steps = trace_to_ui_steps(trace)
    assert steps[0]["action"] == "goto"
    assert steps[1]["action"] == "fill"
    assert all(s["action"] != "done" for s in steps)
    assert all(s.get("selector") != "#x" for s in steps)

    code = trace_to_playwright_code(title="登录", base_url="https://app.example.com", trace=trace)
    assert code.startswith("def run(page):")
    assert 'page.goto("/login")' in code
    assert ".fill(" in code
    assert "get_by_role" in code
    assert "page.locator(\"#x\")" not in code
    assert "import { test, expect }" not in code

    compiled = steps_to_script(
        [
            {"action": "goto", "value": "/"},
            {"action": "fill", "element": "LoginPage.user", "value": "{{user}}"},
        ],
        elements={"LoginPage.user": "#user"},
    )
    assert 'page.locator("#user").fill(env["user"])' in compiled


def test_steps_to_script_stable_locators():
    code = steps_to_script(
        [
            {"action": "fill", "selector": "label=账号", "value": "admin"},
            {"action": "fill", "selector": "placeholder=请输入图中字符", "value": "qzu8"},
            {"action": "click", "selector": 'role=button[name="登录"]'},
            {"action": "click", "selector": 'role=checkbox[name="记住密码"]'},
            {"action": "click", "selector": "#title"},
        ]
    )
    assert 'page.get_by_label("账号", exact=True).fill("admin")' in code
    assert 'page.get_by_placeholder("请输入图中字符", exact=True).fill("qzu8")' in code
    assert 'page.get_by_role("button", name="登录", exact=True).click()' in code
    assert 'page.get_by_role("checkbox", name="记住密码", exact=True).click()' in code
    assert 'page.locator("#title").click()' in code


def test_selector_to_dsl():
    assert selector_to_dsl("testid=user") == '[data-testid="user"]'
    assert 'role=button' in (selector_to_dsl('role=button[name=提交]') or "")


def test_ui_ai_explore_api_confirm_save(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("uia_a", "p")
    tok = _login("uia_a", "p")
    pid = _project(tok, "UIA1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={
            "name": "local",
            "base_url": "https://app.example.com",
            "is_default": True,
        },
    )
    assert er.status_code == 201, er.text
    env_id = er.json()["id"]

    tc = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "打开首页",
            "steps": [{"order": 1, "action": "打开首页", "expected": "可见首页"}],
        },
    )
    assert tc.status_code == 201, tc.text
    testcase_id = tc.json()["id"]

    def fake_apply_async(args=None, **kwargs):
        from app.models.ui_automation.ui_automation import UiAiExploreJob

        job_id = args[0]
        db = TestingSessionLocal()
        try:
            job = db.get(UiAiExploreJob, job_id)
            assert job is not None
            job.status = "succeeded"
            job.plan_json = {"goal": "打开首页", "start_path": "/", "checklist": []}
            job.trace_json = [
                {"action": "goto", "value": "/", "status": "ok"},
                {"action": "assert_url", "value": "/", "status": "ok"},
                {"action": "done", "status": "ok"},
            ]
            job.result_steps_json = [
                {"action": "goto", "value": "/"},
                {"action": "assert_url", "value": "/"},
            ]
            job.playwright_code = 'def run(page):\n    page.goto("/")\n'
            job.dry_run_status = "passed"
            job.dry_run_error = ""
            db.commit()
        finally:
            db.close()
        m = MagicMock()
        m.id = f"task-{job_id}"
        return m

    with patch("app.tasks.ui_automation.run_ui_ai_explore.run_ui_ai_explore_task.apply_async", side_effect=fake_apply_async):
        jr = client.post(
            f"/api/v1/projects/{pid}/ui-ai/explore-jobs",
            headers=h,
            json={"testcase_id": testcase_id, "env_id": env_id, "browser": "chromium"},
        )
    assert jr.status_code == 201, jr.text
    job_id = jr.json()["id"]
    assert jr.json()["status"] == "succeeded"

    gr = client.get(f"/api/v1/projects/{pid}/ui-ai/explore-jobs/{job_id}", headers=h)
    assert gr.status_code == 200
    assert len(gr.json()["result_steps"]) == 2

    sr = client.post(
        f"/api/v1/projects/{pid}/ui-ai/explore-jobs/{job_id}/confirm-save",
        headers=h,
        json={"name": "打开首页 (AI)", "status": "ready"},
    )
    assert sr.status_code == 201, sr.text
    assert sr.json()["name"] == "打开首页 (AI)"
    assert sr.json()["status"] == "ready"
    assert sr.json()["manual_case_id"] == testcase_id
    assert "def run(page):" in sr.json()["script"]
    assert 'page.goto("/")' in sr.json()["script"]
    assert sr.json()["steps"] == []

    _user("uia_b", "p")
    tok_b = _login("uia_b", "p")
    pid_b = _project(tok_b, "UIA2")
    cross = client.get(
        f"/api/v1/projects/{pid_b}/ui-ai/explore-jobs/{job_id}",
        headers={"Authorization": f"Bearer {tok_b}"},
    )
    assert cross.status_code == 404


def test_build_plan_from_testcase_includes_data_and_skips_placeholder(monkeypatch):
    captured: dict = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return (
            '{"goal":"搜索房源","start_path":"/list","checklist":["搜索"],'
            '"matrix":[{"id":"TC-01","name":"搜索","start_url":"/list",'
            '"preconditions":"已登录","steps":["输入关键词"],"data":"三室两厅","expected":"列表匹配"}]}'
        )

    monkeypatch.setattr("app.services.ui_automation.ui_ai_explorer.llm.chat_complete", fake_chat)
    plan = build_plan_from_testcase(
        title="搜索公寓",
        steps=[{"action": "打开列表并搜索", "expected": "—"}],
        base_url="https://ex.com",
        llm_cfg=LlmRuntime(api_base="https://example.com/v1", api_key="k", model="m", allowed_models_csv=""),
        test_data="关键词 三室两厅",
        overall_expected="列表展示匹配房源",
        precondition="已登录后台",
        extra_prompt="优先点筛选栏",
        skip_login=True,
    )
    user = captured["messages"][1]["content"]
    system = captured["messages"][0]["content"]
    assert "关键词 三室两厅" in user
    assert "列表展示匹配房源" in user
    assert "已登录后台" in user
    assert "优先点筛选栏" in user
    assert "期望: —" not in user
    assert "不要规划登录" in system
    assert "不要输出 matrix" in system
    assert plan["matrix"] == []
    assert plan["_single_case"] is True
    assert plan["case_name"] == "搜索公寓"
    assert plan["test_data"] == "关键词 三室两厅"
    assert plan["expected"] == "列表展示匹配房源"


def test_build_plan_from_testcase_drops_llm_matrix(monkeypatch):
    def fake_chat(messages, **kwargs):
        return (
            '{"goal":"登录","start_path":"/login","checklist":["登录"],'
            '"matrix":['
            '{"id":"TC-001","name":"登录成功","start_url":"/login","steps":["填账号"],'
            '"data":"wrong","expected":"ok"},'
            '{"id":"TC-002","name":"验证码错误","start_url":"/login","steps":["错验证码"],'
            '"data":"","expected":"失败"}'
            "]}"
        )

    monkeypatch.setattr("app.services.ui_automation.ui_ai_explorer.llm.chat_complete", fake_chat)
    plan = build_plan_from_testcase(
        title="使用正确账号密码及图片验证码登录成功",
        steps=[{"action": "输入账号和密码", "expected": "—"}],
        base_url="https://ex.com",
        llm_cfg=LlmRuntime(api_base="https://example.com/v1", api_key="k", model="m", allowed_models_csv=""),
        test_data="账号：admin；密码：admin123",
        overall_expected="登录成功，跳转至系统首页",
        precondition="已打开登录页面",
    )
    assert plan["matrix"] == []
    assert plan["_single_case"] is True
    assert plan["test_data"] == "账号：admin；密码：admin123"
