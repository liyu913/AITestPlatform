"""Tests for explore-grounded locator ranking and allowlist."""

from app.services.ui_automation.ui_locator_stability import (
    TIER_ROLE,
    TIER_TESTID,
    collect_explored_locators,
    extract_step_locators,
    locator_tier,
    locators_allowed,
    normalize_locator_expr,
    rank_locator_exprs,
)
from app.services.ui_automation.ui_node_script import _fill_statement, matrix_row_to_node_script


def test_locator_tier_order():
    assert locator_tier('page.getByTestId("user")') == TIER_TESTID
    assert locator_tier('page.getByRole("button", { name: "登录" })') == TIER_ROLE
    assert locator_tier('page.getByTestId("a")') < locator_tier('page.getByRole("button")')


def test_rank_prefers_testid():
    ranked = rank_locator_exprs(
        [
            'page.getByRole("textbox", { name: "账号" })',
            'page.getByTestId("username")',
            'page.getByPlaceholder("请输入")',
        ]
    )
    assert "getByTestId" in ranked[0]


def test_extract_step_prefers_cli_locator_testid():
    locs = extract_step_locators(
        {
            "cli_locator": 'page.getByTestId("user")',
            "role": "textbox",
            "name": "账号",
            "placeholder": "请输入账号",
        }
    )
    assert locs
    assert "getByTestId" in locs[0]


def test_fill_keeps_explore_testid_despite_username_blob():
    """Codegen must not rewrite explore testid into invented 账号 role name."""
    stmt = _fill_statement(
        {
            "action": "fill",
            "cli_locator": 'page.getByTestId("user")',
            "name": "账号",
            "locate": "账号输入框",
            "value": "admin",
        },
        username="admin",
        password="x",
    )
    text = stmt if isinstance(stmt, str) else "\n".join(stmt)
    assert "getByTestId" in text
    assert 'name: "账号"' not in text
    assert "env.username" in text or "admin" in text


def test_matrix_codegen_uses_explore_cli_locator():
    code = matrix_row_to_node_script(
        {
            "id": "TC-01",
            "name": "登录",
            "start_url": "/login",
            "steps": [
                {"action": "goto", "value": "/login", "ready": True},
                {
                    "action": "fill",
                    "cli_locator": 'page.getByTestId("username")',
                    "name": "账号",
                    "value": "u",
                    "ready": True,
                },
                {
                    "action": "click",
                    "cli_locator": 'page.getByRole("button", { name: "登录" })',
                    "target": "新建项目",
                    "ready": True,
                },
            ],
        },
        username="u",
        password="p",
    )
    assert "getByTestId" in code
    assert "登录" in code
    assert "新建项目" not in code


def test_collect_explored_and_allowlist():
    row = {
        "id": "TC-01",
        "steps": [
            {
                "action": "click",
                "cli_locator": 'page.getByRole("button", { name: "新建项目" })',
            },
            {
                "action": "click",
                "role": "button",
                "name": "登录",
            },
        ],
    }
    locs = collect_explored_locators(matrix_row=row, case_id="TC-01")
    assert any("新建项目" in x for x in locs)
    assert any("登录" in x for x in locs)

    good = """async function run(page, agent) {
  await page.getByRole("button", { name: "登录" }).click();
}
"""
    bad = """async function run(page, agent) {
  await page.getByRole("button", { name: "不存在的按钮" }).click();
}
"""
    assert locators_allowed(good, locs) is True
    assert locators_allowed(bad, locs) is False
    assert normalize_locator_expr("page.getByRole('button', { name: \"登录\" })") == normalize_locator_expr(
        'page.getByRole("button", { name: "登录" })'
    )
