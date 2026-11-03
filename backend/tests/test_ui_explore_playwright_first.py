"""Playwright-first explore: dialog snapshot depth, locator ladder, Midscene step fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.ui_automation.ui_ai_explorer import (
    _MIDSCENE_FAIL_REASON,
    _build_playwright_candidates,
    _hint_from_selector,
    _simplify_aria,
    _try_playwright_click_or_fill,
    apply_browser_action,
    run_explore_loop,
    take_snapshot,
    trace_to_explore_script,
)


def _ant_modal_tree() -> dict:
    """Ant Design-like nesting: dialog → generic wrappers → form-item → textbox."""
    return {
        "role": "WebArea",
        "name": "项目管理",
        "children": [
            {
                "role": "dialog",
                "name": "新建项目",
                "children": [
                    {
                        "role": "generic",
                        "name": "",
                        "children": [
                            {
                                "role": "generic",
                                "name": "",
                                "children": [
                                    {
                                        "role": "generic",
                                        "name": "",
                                        "children": [
                                            {
                                                "role": "form",
                                                "name": "",
                                                "children": [
                                                    {
                                                        "role": "generic",
                                                        "name": "",
                                                        "children": [
                                                            {
                                                                "role": "textbox",
                                                                "name": "名称",
                                                                "children": [],
                                                            },
                                                            {
                                                                "role": "textbox",
                                                                "name": "描述",
                                                                "children": [],
                                                            },
                                                        ],
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {"role": "button", "name": "取消", "children": []},
                    {"role": "button", "name": "创建", "children": []},
                ],
            }
        ],
    }


def test_simplify_aria_keeps_dialog_textboxes_past_generic_wrappers():
    lines = _simplify_aria(_ant_modal_tree(), depth=0, limit=6)
    blob = "\n".join(lines)
    assert "dialog: 新建项目" in blob
    assert "textbox: 名称" in blob
    assert "textbox: 描述" in blob
    assert "button: 创建" in blob
    # nameless generics should not appear as their own lines
    assert not any(line.strip() == "generic:" for line in lines)


def test_hint_from_selector_strips_required_star():
    hints = _hint_from_selector('role=textbox[name="* 名称"]')
    assert hints["role"] == "textbox"
    assert hints["name"] == "名称"
    assert _hint_from_selector("placeholder=例如：移动端 2.0")["placeholder"] == "例如：移动端 2.0"
    assert _hint_from_selector("label=名称")["label"] == "名称"


def test_build_candidates_prefer_role_label_placeholder(monkeypatch):
    root = MagicMock()
    role_loc = MagicMock(name="role")
    label_loc = MagicMock(name="label")
    ph_loc = MagicMock(name="ph")
    root.get_by_role.return_value = role_loc
    root.get_by_label.return_value = label_loc
    root.get_by_placeholder.return_value = ph_loc

    cands = _build_playwright_candidates(
        root,
        action="fill",
        selector="text=名称",
        decision={"placeholder": "例如：移动端 2.0"},
    )
    dsls = [d for d, _ in cands]
    assert any(d.startswith("role=textbox") for d in dsls)
    assert "label=名称" in dsls
    assert "placeholder=例如：移动端 2.0" in dsls
    # fill must not lead with bare text= as the only strategy; text= may be absent for fill
    assert "text=名称" not in dsls


def test_build_candidates_captcha_field_uses_snapshot_name_not_raina_ph():
    root = MagicMock()
    root.get_by_role.return_value = MagicMock()
    root.get_by_label.return_value = MagicMock()
    root.get_by_placeholder.return_value = MagicMock()

    cands = _build_playwright_candidates(
        root,
        action="fill",
        selector='role=textbox[name="验证码"]',
        decision={},
    )
    dsls = [d for d, _ in cands]
    assert 'role=textbox[name="验证码"]' in dsls
    assert "label=验证码" in dsls
    assert "placeholder=请输入图中字符" not in dsls
    assert "placeholder=验证码" not in dsls

    remapped = _build_playwright_candidates(
        root,
        action="fill",
        selector="placeholder=验证码",
        decision={"locate": "验证码输入框", "name": "验证码"},
    )
    remapped_dsls = [d for d, _ in remapped]
    assert "placeholder=请输入图中字符" not in remapped_dsls
    assert "placeholder=验证码" not in remapped_dsls
    assert 'role=textbox[name="验证码"]' in remapped_dsls


def test_try_playwright_falls_through_to_placeholder():
    page = MagicMock()
    dialog = MagicMock()
    dialog.count.return_value = 1
    dialog.nth.return_value.is_visible.return_value = True
    page.get_by_role.return_value = dialog

    failing = MagicMock()
    failing.first.wait_for.side_effect = TimeoutError("not visible")
    winning = MagicMock()
    winning.first.wait_for.return_value = None
    winning.first.fill.return_value = None

    # dialog-scoped lookups
    dialog_root = dialog.nth.return_value
    dialog_root.get_by_role.return_value = failing
    dialog_root.get_by_label.return_value = failing
    dialog_root.get_by_placeholder.return_value = winning
    dialog_root.get_by_text.return_value = failing
    dialog_root.locator.return_value = failing

    with patch(
        "app.services.ui_automation.ui_ai_explorer._scope_root",
        return_value=dialog_root,
    ):
        hit = _try_playwright_click_or_fill(
            page=page,
            action="fill",
            selector='role=textbox[name="名称"]',
            value="移动端 2.0",
            decision={"placeholder": "例如：移动端 2.0"},
            timeout_ms=2000,
        )
    assert hit["driver"] == "playwright"
    assert hit["selector"] == "placeholder=例如：移动端 2.0"
    winning.first.fill.assert_called()


def test_apply_browser_action_midscene_after_three_playwright_failures():
    page = MagicMock()
    decision = {"action": "click", "selector": 'role=button[name="创建"]'}

    with (
        patch(
            "app.services.ui_automation.ui_ai_explorer._try_playwright_click_or_fill",
            side_effect=RuntimeError(f"{_MIDSCENE_FAIL_REASON}: boom"),
        ),
        patch(
            "app.services.ui_automation.ui_ai_explorer._fallback_midscene_step",
            return_value={
                "driver": "midscene",
                "reason": _MIDSCENE_FAIL_REASON,
                "prompt": "「创建」按钮或可点击控件",
                "locate": "「创建」按钮或可点击控件",
                "kind": "ai_tap",
                "selector": 'role=button[name="创建"]',
            },
        ) as mid,
    ):
        detail = apply_browser_action(
            page=page,
            base_url="https://example.com",
            decision=decision,
            ctx={},
            timeout_ms=3000,
            cdp_endpoint="http://127.0.0.1:9222",
            model_config={"OPENAI_API_KEY": "x"},
        )
    assert detail["driver"] == "midscene"
    assert detail["reason"] == _MIDSCENE_FAIL_REASON
    mid.assert_called_once()


def test_run_explore_loop_continues_after_step_error():
    page = MagicMock()
    page.url = "https://example.com/"
    page.goto.return_value = None
    page.wait_for_load_state.return_value = None
    page.accessibility.snapshot.return_value = {"role": "WebArea", "name": "x", "children": []}
    page.evaluate.return_value = []

    decisions = [
        {"action": "click", "selector": "#missing"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with (
        patch("app.services.ui_automation.ui_ai_explorer.decide_next_action", side_effect=fake_decide),
        patch(
            "app.services.ui_automation.ui_ai_explorer.apply_browser_action",
            side_effect=RuntimeError("still failed after midscene"),
        ),
        patch("app.services.ui_automation.ui_ai_explorer.assert_same_host"),
        patch("app.services.ui_automation.ui_ai_explorer.join_base_and_path", return_value="https://example.com/"),
    ):
        trace = run_explore_loop(
            page=page,
            base_url="https://example.com",
            plan={"start_path": "/", "goal": "新建项目"},
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=5,
            timeout_ms=1000,
        )
    statuses = [t.get("status") for t in trace]
    assert "error" in statuses
    assert any(t.get("action") == "done" and t.get("status") == "ok" for t in trace)


def test_trace_to_explore_script_uses_node_when_midscene_step():
    trace = [
        {"action": "goto", "value": "/", "status": "ok"},
        {
            "action": "fill",
            "selector": "placeholder=例如：移动端 2.0",
            "value": "demo",
            "status": "ok",
            "driver": "playwright",
        },
        {
            "action": "click",
            "selector": 'role=button[name="创建"]',
            "status": "ok",
            "driver": "midscene",
            "locate": "创建按钮",
            "kind": "ai_tap",
        },
    ]
    code = trace_to_explore_script(title="t", base_url="https://x", trace=trace, start_path="/")
    assert code.startswith("async function run(page, agent)")
    assert "getByPlaceholder" in code or ".fill(" in code
    assert "agent.aiTap" in code


def test_take_snapshot_appends_dom_form_hints():
    page = MagicMock()
    page.accessibility.snapshot.return_value = {
        "role": "dialog",
        "name": "新建项目",
        "children": [{"role": "textbox", "name": "名称", "children": []}],
    }
    page.evaluate.return_value = [
        {"tag": "input", "label": "名称", "placeholder": "例如：移动端 2.0", "name": ""},
        {"tag": "textarea", "label": "描述", "placeholder": "一句话说明项目范围（可选）", "name": ""},
    ]
    snap = take_snapshot(page)
    assert "textbox: 名称" in snap
    assert "form-fields:" in snap
    assert "placeholder=例如：移动端 2.0" in snap
    assert "label=描述" in snap
