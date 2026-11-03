from app.services.ui_automation.ui_node_script import (
    _assert_statements,
    actions_to_node_script,
    is_node_ui_script,
    is_runnable_ui_script,
    midscene_dump_to_actions,
)


def test_assert_url_login_success_waits_before_expect():
    lines = _assert_statements(
        {"action": "assert_url", "value": "登录成功，跳转至系统首页"},
        start_path="/login",
    )
    assert any("waitForURL" in ln for ln in lines)
    assert any("not.toHaveURL" in ln for ln in lines)
    assert any("15000" in ln for ln in lines)
    joined = "\n".join(lines)
    assert "login" in joined.lower()
    assert "signin" not in joined.lower()  # no hardcoded login|signin alternation


def test_assert_url_leave_uses_start_path_not_hardcoded_login():
    lines = _assert_statements(
        {"action": "assert_url", "value": "离开登录页"},
        start_path="/auth/sign-in",
    )
    joined = "\n".join(lines)
    assert "auth/sign-in" in joined
    assert "signin" not in joined.lower()  # no login|signin hardcode


def test_assert_url_explicit_path_keeps_tohaveurl():
    lines = _assert_statements({"action": "assert_url", "value": "/dashboard"})
    assert any("toHaveURL" in ln for ln in lines)
    assert not any("waitForURL" in ln for ln in lines)


def test_ensure_submit_does_not_inject_hardcoded_submit_label():
    code = actions_to_node_script(
        [
            {"kind": "goto", "path": "/auth"},
            {"kind": "fill", "locate": "Email", "value": "a@b.c"},
        ],
        start_path="/auth",
        ensure_submit=True,
    )
    assert 'aiTap("提交")' not in code
    assert "aiTap(" not in code


def test_assert_text_truncate_skips_product_domain_tokens():
    lines = _assert_statements(
        {
            "action": "assert_text",
            "value": "操作完成并且成功进入系统首页，请继续查看项目与需求列表中的数据项",
        }
    )
    assert lines
    # Prefer generic success/home tokens, not 项目/需求
    assert "项目" not in lines[0] or "成功" in lines[0] or "首页" in lines[0]
    assert 'getByText("项目")' not in lines[0]
    assert 'getByText("需求")' not in lines[0]


def test_script_shape_detects_python_and_node():
    assert is_runnable_ui_script("def run(page):\n    page.goto('/')\n")
    assert not is_node_ui_script("def run(page):\n    pass\n")
    node = "async function run(page, agent) {\n  await page.goto('/');\n}\n"
    assert is_node_ui_script(node)
    assert is_runnable_ui_script(node)
    assert not is_runnable_ui_script("console.log('no')")


def test_midscene_dump_compiles_login_with_captcha():
    dump = {
        "executions": [
            {
                "name": "aiAct",
                "tasks": [
                    {
                        "type": "Planning",
                        "output": {
                            "yamlFlow": [
                                {"aiInput": "admin", "locate": "账号输入框"},
                                {"aiInput": "secret", "locate": "密码输入框"},
                                {"aiInput": "a1b2", "locate": "验证码输入框"},
                                {"aiTap": "登录按钮", "locate": "登录按钮"},
                            ]
                        },
                    }
                ],
            }
        ]
    }
    actions = midscene_dump_to_actions(dump)
    code = actions_to_node_script(
        actions,
        username="admin",
        password="secret",
        start_path="/login",
        ensure_submit=True,
    )
    assert code.startswith("async function run(page, agent)")
    assert 'page.goto("/login")' in code
    assert "env.username" in code
    assert "env.password" in code
    assert "getByRole('textbox'" in code
    assert "agent.aiQuery" in code
    assert ".fill(captcha)" in code
    assert 'agent.aiInput("验证码图片中的字符"' not in code
    assert "a1b2" not in code
    assert "secret" not in code
    assert 'getByRole("button", { name: "登录" })' in code


def test_midscene_dump_nested_prompt_objects():
    dump = {
        "executions": [
            {
                "name": "aiAct",
                "tasks": [
                    {
                        "type": "Planning",
                        "output": {
                            "yamlFlow": [
                                {
                                    "aiTap": {"prompt": "提交按钮"},
                                    "locate": {
                                        "promptDisplay": "页面底部的提交",
                                        "prompt": {"prompt": "提交按钮", "images": []},
                                    },
                                }
                            ],
                            "actions": [
                                {
                                    "type": "Tap",
                                    "param": {
                                        "locate": {
                                            "prompt": {"prompt": "登录按钮"},
                                        }
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ]
    }
    actions = midscene_dump_to_actions(dump)
    assert actions
    assert actions[0]["kind"] == "click"
    assert actions[0]["locate"] == "页面底部的提交"
    assert "[object Object]" not in actions[0]["locate"]


def test_captcha_fill_uses_aiquery_not_hardcoded_chars():
    code = actions_to_node_script(
        [
            {"kind": "goto", "path": "/login"},
            {
                "kind": "fill_captcha",
                "name": "验证码",
                "cli_locator": 'page.getByPlaceholder("Enter code")',
                "value": "8n7",
            },
            {"kind": "click", "role": "button", "name": "登录"},
        ],
        start_path="/login",
    )
    assert "agent.aiQuery" in code
    assert ".fill(captcha)" in code
    assert 'getByPlaceholder("Enter code")' in code
    assert 'getByPlaceholder("请输入图中字符")' not in code
    assert "8n7" not in code


def test_captcha_fill_locator_prefers_trace_fields_not_raina_default():
    from app.services.ui_automation.ui_node_script import _captcha_fill_locator

    assert "验证码输入框" in _captcha_fill_locator({"locate": "验证码输入框"})
    assert 'name: "验证码"' in _captcha_fill_locator({"name": "验证码"})
    assert "Enter the code" in _captcha_fill_locator({"placeholder": "Enter the code"})
    # Short captcha label as placeholder → not used as getByPlaceholder
    loc = _captcha_fill_locator({"placeholder": "验证码", "name": "验证码"})
    assert 'getByPlaceholder("验证码")' not in loc
    assert 'name: "验证码"' in loc
    assert "cli_locator" in str(
        _captcha_fill_locator({"cli_locator": 'page.getByRole("textbox", { name: "Code" })'})
    ) or "Code" in _captcha_fill_locator(
        {"cli_locator": 'page.getByRole("textbox", { name: "Code" })'}
    )


def test_getbyrole_selector_not_wrapped_in_page_locator():
    """Suite runners fail if getByRole(...) is passed to page.locator as CSS."""
    from app.services.ui_automation.ui_node_script import _fill_statement, _page_locator_expr

    raw = "getByRole('textbox', { name: '* 名称' })"
    expr = _page_locator_expr(raw)
    assert expr.startswith("page.getByRole")
    assert 'page.locator("getByRole' not in expr
    assert "page.locator('getByRole" not in expr

    stmt = _fill_statement(
        {"action": "fill", "selector": raw, "value": "demo"},
        username="u",
        password="p",
    )
    assert isinstance(stmt, str)
    assert "page.getByRole" in stmt
    assert 'page.locator("getByRole' not in stmt
    assert ".fill(" in stmt


def test_py_locator_getbyrole_not_css():
    from app.services.ui_automation.ui_ai_explorer import _py_locator

    out = _py_locator("getByRole('textbox', { name: '* 名称' })")
    assert "get_by_role" in out
    assert 'page.locator("getByRole' not in out


def test_snapshot_ref_not_emitted_as_page_locator():
    from app.services.ui_automation.ui_node_script import (
        _click_statement,
        _fill_statement,
        _page_locator_expr,
    )

    for raw in ("e23", "ref=e23", 'page.locator("e23")', "page.locator('e12')"):
        assert "locator(" not in (_page_locator_expr(raw) or "")
        assert "e23" not in (_page_locator_expr(raw) or "")

    click = _click_statement(
        {"action": "click", "role": "button", "name": "登录", "selector": "e23"}
    )
    assert "getByRole" in click
    assert 'locator("e23")' not in click

    click_ref_only = _click_statement({"action": "click", "selector": "e23"})
    assert "aiTap" in click_ref_only
    assert 'locator("e23")' not in click_ref_only

    fill = _fill_statement(
        {"action": "fill", "selector": "e23", "locate": "账号", "value": "admin"},
        username="admin",
        password="x",
    )
    text = fill if isinstance(fill, str) else "\n".join(fill)
    assert 'locator("e23")' not in text


def test_compile_trace_ref_only_uses_aitap():
    from app.services.ui_automation.ui_cli_explorer import compile_cli_trace_script

    code = compile_cli_trace_script(
        [
            {"kind": "goto", "path": "/", "status": "ok", "driver": "playwright"},
            {
                "kind": "click",
                "status": "ok",
                "driver": "playwright",
                "selector": "e23",
                "cli_locator": 'page.locator("e23")',
            },
        ],
        start_path="/",
    )
    assert 'locator("e23")' not in code
    assert "aiTap" in code
