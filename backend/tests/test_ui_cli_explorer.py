"""Playwright-cli explore loop + codegen (mocked CLI session)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.ui_automation.playwright_cli_session import (
    is_element_ref,
    is_snapshot_ref_locator,
    normalize_ref,
)
from app.services.ui_automation.ui_cli_explorer import (
    actions_from_cli_trace,
    compile_cli_trace_script,
    run_cli_explore_loop,
)
from app.services.ui_automation.ui_explore_engines import run_playwright_cli_engine
from app.services.ui_automation.ui_explore_report import matrix_bind_issues


def test_ref_helpers():
    assert is_element_ref("e12")
    assert is_element_ref("ref=e3")
    assert not is_element_ref("role=button[name=登录]")
    assert normalize_ref("ref=e9") == "e9"
    assert is_snapshot_ref_locator("e23")
    assert is_snapshot_ref_locator('page.locator("e23")')
    assert not is_snapshot_ref_locator('page.getByRole("button", { name: "登录" })')
    assert not is_snapshot_ref_locator('page.locator("#username")')


def test_locator_fields_from_snapshot():
    from app.services.ui_automation.ui_cli_explorer import locator_fields_from_snapshot

    snap = '- button "新建项目" [ref=e23] [cursor=pointer]\n- textbox "账号" [ref=e12]'
    assert locator_fields_from_snapshot(snap, "e23") == {"role": "button", "name": "新建项目"}
    assert locator_fields_from_snapshot(snap, "ref=e12") == {"role": "textbox", "name": "账号"}


def test_compile_cli_trace_prefers_playwright_locators():
    trace = [
        {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
        {
            "kind": "fill",
            "action": "fill",
            "status": "ok",
            "driver": "playwright",
            "cli_locator": 'page.getByRole("textbox", { name: "账号" })',
            "selector": 'page.getByRole("textbox", { name: "账号" })',
            "value": "admin",
        },
        {
            "kind": "fill_captcha",
            "action": "fill",
            "status": "ok",
            "driver": "playwright",
            "captcha": True,
            "placeholder": "请输入图中字符",
            "locate": "验证码输入框",
        },
        {
            "kind": "click",
            "action": "click",
            "status": "ok",
            "driver": "midscene",
            "locate": "图表放大按钮",
            "prompt": "图表放大按钮",
        },
    ]
    code = compile_cli_trace_script(trace, start_path="/login", username="admin")
    assert code.startswith("async function run(page, agent)")
    assert "getByRole" in code or "cli_locator" in str(actions_from_cli_trace(trace))
    assert "agent.aiQuery" in code
    assert 'getByPlaceholder("请输入图中字符")' in code  # from trace placeholder
    assert 'getByPlaceholder("验证码")' not in code
    assert "agent.aiTap" in code
    assert "aiAct(" not in code


def test_snapshot_has_captcha_error_generic_copy():
    from app.services.ui_automation.ui_cli_explorer import snapshot_has_captcha_error
    from app.user_messages import CAPTCHA_INVALID

    assert snapshot_has_captcha_error(f"- paragraph: {CAPTCHA_INVALID}") is True
    assert snapshot_has_captcha_error("- alert: Invalid captcha code") is True
    assert snapshot_has_captcha_error("- text: captcha expired") is True
    assert snapshot_has_captcha_error('- button "登录"') is False
    assert snapshot_has_captcha_error("") is False


def test_run_cli_explore_loop_executes_generic_midscene():
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- textbox "账号" [ref=e1]\n- button "登录" [ref=e2]'
    fake_cli.generate_locator.return_value = 'page.getByRole("textbox", { name: "账号" })'
    fake_cli.cdp_endpoint = "http://127.0.0.1:9222"
    fake_cli.timeout_ms = 5000
    decisions = [
        {"action": "fill", "selector": "e1", "value": "admin"},
        {"action": "click", "driver": "midscene", "prompt": "特殊按钮"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with (
        patch(
            "app.services.ui_automation.ui_explore_graph.decide_next_action",
            side_effect=fake_decide,
        ),
        patch(
            "app.services.ui_automation.node_ui_runner.midscene_step_over_cdp",
            return_value={"ok": True},
        ) as mock_mid,
    ):
        trace = run_cli_explore_loop(
            base_url="https://example.com",
            plan={"goal": "登录", "start_path": "/login"},
            ctx={"username": "admin", "password": "x"},
            llm_cfg=MagicMock(),
            max_steps=5,
            session=fake_cli,
            midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
        )
    fake_cli.open.assert_called_once()
    fake_cli.fill.assert_called()
    fake_cli.click.assert_not_called()
    mock_mid.assert_called()
    mid_rows = [t for t in trace if t.get("driver") == "midscene"]
    assert mid_rows
    assert not any(t.get("explore_noop") for t in mid_rows)
    assert any(t.get("explore_midscene_act") for t in mid_rows)
    assert mock_mid.call_args.kwargs.get("action") == "tap"
    assert "aiAct" not in str(trace)


def test_apply_cli_decision_generic_midscene_fill():
    from app.services.ui_automation.ui_cli_explorer import apply_cli_decision

    fake_cli = MagicMock()
    fake_cli.cdp_endpoint = "http://127.0.0.1:9222"
    fake_cli.timeout_ms = 5000
    with patch(
        "app.services.ui_automation.node_ui_runner.midscene_step_over_cdp",
        return_value={"ok": True},
    ) as mock_mid:
        rows, _ = apply_cli_decision(
            fake_cli,
            {
                "action": "fill",
                "driver": "midscene",
                "prompt": "画布上的搜索框",
                "value": "hello",
            },
            base_url="https://example.com",
            ctx={},
            url="https://example.com/",
            midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
        )
    mock_mid.assert_called_once()
    assert mock_mid.call_args.kwargs["action"] == "fill"
    assert mock_mid.call_args.kwargs["value"] == "hello"
    assert rows[0].get("explore_midscene_act") is True
    assert rows[0].get("explore_noop") is not True
    assert rows[0].get("status") == "ok"


def test_apply_cli_decision_midscene_skip_without_cdp():
    from app.services.ui_automation.ui_cli_explorer import apply_cli_decision

    fake_cli = MagicMock()
    fake_cli.cdp_endpoint = ""
    rows, _ = apply_cli_decision(
        fake_cli,
        {"action": "click", "driver": "midscene", "prompt": "图标按钮"},
        base_url="https://example.com",
        ctx={},
        url="https://example.com/",
        midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
    )
    assert rows[0].get("status") == "error"
    assert "CDP" in str(rows[0].get("error") or "")
    assert rows[0].get("explore_noop") is not True


def test_bind_fill_is_pass_through_agent_owns_value():
    from app.services.ui_automation.ui_cli_explorer import bind_fill_value_from_ctx

    # Agent value is not overridden by ctx (even if ctx looks "cleaner")
    kept = bind_fill_value_from_ctx(
        {"action": "fill", "name": "账号", "value": "admin"},
        {"username": "other", "password": "x"},
    )
    assert kept["value"] == "admin"
    # Still pass-through for outcome text — decide prompt must prevent this, not bind
    raw = bind_fill_value_from_ctx(
        {"action": "fill", "name": "账号", "value": "登录成功"},
        {"username": "admin"},
    )
    assert raw["value"] == "登录成功"


def test_apply_cli_decision_clear_then_fill_username():
    from app.services.ui_automation.ui_cli_explorer import apply_cli_decision

    fake_cli = MagicMock()
    fake_cli.cdp_endpoint = None
    fake_cli.snapshot.return_value = '- textbox "账号" [ref=e10]'
    fake_cli.generate_locator.return_value = 'page.getByRole("textbox", { name: "账号" })'
    apply_cli_decision(
        fake_cli,
        {"action": "fill", "selector": "e10", "name": "账号", "value": "admin"},
        base_url="https://example.com",
        ctx={"username": "admin", "password": "x"},
        url="https://example.com/login",
    )
    # clear then fill with Agent-provided value
    assert fake_cli.fill.call_count >= 2
    assert fake_cli.fill.call_args_list[0].args[1] == ""
    assert fake_cli.fill.call_args_list[-1].args[1] == "admin"


def test_midscene_fingerprint_includes_non_captcha_fill():
    from app.services.ui_automation.ui_cli_explorer import midscene_noop_fingerprint

    fp = midscene_noop_fingerprint(
        {"driver": "midscene", "action": "fill", "prompt": "画布搜索框", "value": "x"}
    )
    assert fp.startswith("midscene::fill::")
    assert "画布搜索框" in fp


def test_run_playwright_cli_engine_compiles(monkeypatch):
    def fake_loop(**kwargs):
        return [
            {"kind": "goto", "path": "/", "status": "ok", "driver": "playwright"},
            {
                "kind": "click",
                "action": "click",
                "status": "ok",
                "driver": "playwright",
                "role": "button",
                "name": "新建项目",
            },
        ]

    monkeypatch.setattr(
        "app.services.ui_automation.ui_explore_engines.run_cli_explore_loop",
        fake_loop,
    )
    llm = MagicMock()
    llm.chat_complete.side_effect = RuntimeError("no derive in this test")
    plan: dict = {"goal": "新建项目", "start_path": "/", "matrix": [], "checklist": []}
    trace, script = run_playwright_cli_engine(
        base_url="https://example.com",
        start_path="/",
        goal="新建项目",
        username="",
        password="",
        llm=llm,
        headed=False,
        timeout_ms=5000,
        max_steps=3,
        plan=plan,
    )
    assert trace
    assert "async function run" in script
    assert "aiAct(" not in script
    assert "新建项目" in script or "getByRole" in script
    assert plan.get("case_scripts")
    assert any(c.get("ready") and c.get("code") for c in plan["case_scripts"])


def test_run_playwright_cli_engine_single_case_skips_derive(monkeypatch):
    def fake_loop(**kwargs):
        return [
            {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
            {
                "kind": "fill",
                "status": "ok",
                "driver": "playwright",
                "placeholder": "账号",
                "value": "admin",
            },
            {
                "kind": "click",
                "status": "ok",
                "driver": "playwright",
                "role": "button",
                "name": "登录",
            },
        ]

    monkeypatch.setattr(
        "app.services.ui_automation.ui_explore_engines.run_cli_explore_loop",
        fake_loop,
    )

    def boom(*args, **kwargs):
        raise AssertionError("derive_matrix_from_explore should not run for _single_case")

    monkeypatch.setattr(
        "app.services.ui_automation.ui_explore_engines.derive_matrix_from_explore",
        boom,
    )
    llm = MagicMock()
    plan: dict = {
        "goal": "登录成功",
        "start_path": "/login",
        "matrix": [],
        "checklist": ["登录"],
        "case_name": "使用正确账号密码登录成功",
        "test_data": "账号：admin；密码：admin123",
        "expected": "登录成功，跳转至系统首页",
        "_single_case": True,
    }
    _trace, script = run_playwright_cli_engine(
        base_url="https://example.com",
        start_path="/login",
        goal="登录成功",
        username="admin",
        password="admin123",
        llm=llm,
        headed=False,
        timeout_ms=5000,
        max_steps=10,
        plan=plan,
    )
    assert "async function run" in script
    assert "expect(page)" in script
    assert "toHaveURL" in script
    assert len(plan.get("case_scripts") or []) == 1
    assert plan["case_scripts"][0]["name"] == "使用正确账号密码登录成功"
    assert plan["case_scripts"][0].get("ready")
    row = (plan.get("matrix") or [])[0]
    assert row.get("ready")
    assert row.get("has_assert")
    assert any(str(s.get("action") or "").startswith("assert") for s in (row.get("steps") or []))
    assert matrix_bind_issues(plan.get("matrix")) == []
    llm.chat_complete.assert_not_called()


def test_run_playwright_cli_engine_single_case_without_expected_not_ready(monkeypatch):
    monkeypatch.setattr(
        "app.services.ui_automation.ui_explore_engines.run_cli_explore_loop",
        lambda **kwargs: [
            {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
            {
                "kind": "click",
                "status": "ok",
                "driver": "playwright",
                "role": "button",
                "name": "登录",
            },
        ],
    )
    plan: dict = {
        "goal": "登录",
        "start_path": "/login",
        "matrix": [],
        "case_name": "登录",
        "_single_case": True,
    }
    _trace, script = run_playwright_cli_engine(
        base_url="https://example.com",
        start_path="/login",
        goal="登录",
        username="",
        password="",
        llm=MagicMock(),
        headed=False,
        timeout_ms=5000,
        max_steps=10,
        plan=plan,
    )
    assert "async function run" in script
    assert "expect(page)" not in script
    assert plan["matrix"][0].get("ready") is False
    assert plan["case_scripts"][0].get("ready") is False


def test_run_playwright_cli_engine_explore_first_derives_multi_cases(monkeypatch):
    def fake_loop(**kwargs):
        return [
            {"kind": "goto", "path": "/login", "status": "ok", "driver": "playwright"},
            {
                "kind": "fill",
                "status": "ok",
                "driver": "playwright",
                "placeholder": "账号",
                "value": "admin",
            },
            {
                "kind": "click",
                "status": "ok",
                "driver": "playwright",
                "role": "button",
                "name": "登录",
            },
            {
                "kind": "click",
                "status": "ok",
                "driver": "playwright",
                "name": "项目管理",
            },
            {
                "kind": "click",
                "status": "ok",
                "driver": "playwright",
                "name": "新建项目",
            },
        ]

    monkeypatch.setattr(
        "app.services.ui_automation.ui_explore_engines.run_cli_explore_loop",
        fake_loop,
    )
    llm = MagicMock()
    llm.chat_complete.side_effect = RuntimeError("use fallback split")
    plan: dict = {
        "goal": "登录与项目管理",
        "start_path": "/login",
        "matrix": [],
        "checklist": ["登录", "项目列表", "新建项目"],
        "user_prompt": "登录后看项目并新建",
    }
    _trace, script = run_playwright_cli_engine(
        base_url="https://example.com",
        start_path="/login",
        goal="登录与项目管理",
        username="admin",
        password="secret",
        llm=llm,
        headed=False,
        timeout_ms=5000,
        max_steps=10,
        plan=plan,
    )
    assert "async function run" in script
    ready = [c for c in (plan.get("case_scripts") or []) if c.get("ready") and (c.get("code") or "").strip()]
    assert len(ready) >= 2
    assert len(plan.get("matrix") or []) >= 2


def test_apply_cli_decision_logs_selector_and_masks_password():
    from app.services.ui_automation.ui_cli_explorer import apply_cli_decision

    cli = MagicMock()
    cli.generate_locator.return_value = 'page.getByLabel("密码")'
    logs: list[str] = []
    apply_cli_decision(
        cli,
        {
            "action": "fill",
            "selector": "e1",
            "value": "s3cret",
            "case_id": "UC-001",
            "reason": "填写登录密码",
        },
        base_url="https://example.com",
        ctx={"username": "admin", "password": "s3cret"},
        url="https://example.com/login",
        on_log=logs.append,
    )
    joined = "\n".join(logs)
    assert "UC-001" in joined
    assert "fill" in joined
    assert "e1" in joined or "getByLabel" in joined
    assert "填写登录密码" in joined
    assert "s3cret" not in joined
    assert "***" in joined


def test_apply_cli_decision_uses_agent_password_value_not_ctx_override():
    from app.services.ui_automation.ui_cli_explorer import apply_cli_decision

    cli = MagicMock()
    cli.generate_locator.return_value = 'page.getByLabel("密码")'
    apply_cli_decision(
        cli,
        {
            "action": "fill",
            "selector": "e10",
            "value": "typed-by-agent",
            "reason": "填写登录密码",
        },
        base_url="https://example.com",
        ctx={"username": "admin", "password": "admin123"},
        url="https://example.com/login",
    )
    cli.fill.assert_called()
    # Last fill is the value (after clear ""); Agent value wins over ctx
    assert cli.fill.call_args_list[-1].args[1] == "typed-by-agent"


def test_apply_cli_decision_uses_agent_username_value_not_ctx_override():
    from app.services.ui_automation.ui_cli_explorer import apply_cli_decision

    cli = MagicMock()
    cli.generate_locator.return_value = 'page.getByLabel("账号")'
    apply_cli_decision(
        cli,
        {
            "action": "fill",
            "selector": "e8",
            "value": "guest",
            "reason": "填写账号",
        },
        base_url="https://example.com",
        ctx={"username": "admin", "password": "admin123"},
        url="https://example.com/login",
    )
    assert cli.fill.call_args_list[-1].args[1] == "guest"


def test_apply_cli_decision_skips_problem_text_assert():
    from app.services.ui_automation.ui_cli_explorer import (
        PROBLEM_TEXT_NOT_PROGRESS,
        apply_cli_decision,
    )

    cli = MagicMock()
    rows, _url = apply_cli_decision(
        cli,
        {"action": "assert_text", "value": "请输入项目名称", "selector": "e178"},
        base_url="https://example.com",
        ctx={},
        url="https://example.com/",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"
    assert rows[0]["reason"] == PROBLEM_TEXT_NOT_PROGRESS
    assert rows[0].get("explore_problem_assert_skip") is True


def test_playwright_cli_screenshot_uses_context_page():
    from app.services.ui_automation.playwright_cli_session import PlaywrightCliSession

    page = MagicMock()
    page.screenshot.return_value = b"png-bytes"
    ctx = MagicMock()
    ctx.pages = [page]
    sess = PlaywrightCliSession.__new__(PlaywrightCliSession)
    sess._context = ctx
    sess.cdp_endpoint = "http://127.0.0.1:9222"
    assert sess.screenshot() == b"png-bytes"
    page.screenshot.assert_called()
