"""LangGraph UI explore orchestration tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.ui_automation.ui_cli_explorer import (
    CAPTCHA_NEED_HUMAN,
    CAPTCHA_READ_FINGERPRINT,
    assert_targets_problem_text,
    force_click_submit,
    force_fill_for_problem_hint,
    midscene_noop_fingerprint,
    snapshot_has_captcha_error,
    snapshot_has_problem_hint,
)
import pytest

from app.services.ui_automation.ui_explore_graph import (
    MANUAL_STOP_MESSAGE,
    ExploreRuntime,
    build_ui_explore_graph,
    run_ui_explore_graph,
)
from app.user_messages import CAPTCHA_INVALID

_PROBLEM_SNAP = (
    '- dialog "新建项目"\n'
    '- textbox "项目名称" [ref=e164]\n'
    '- paragraph: 请输入项目名称 [ref=e178]\n'
    '- button "确定" [ref=e180]'
)
_CAPTCHA_PLACEHOLDER_SNAP = (
    '- textbox "请输入图中字符" [ref=e4]\n'
    '- button "登录" [ref=e5]'
)

_CAPTCHA_ERR_SNAP = f'- paragraph: {CAPTCHA_INVALID}\n- button "换一张验证码" [ref=e16]\n- button "登录" [ref=e22]'
_OK_SNAP = '- button "登录" [ref=e22]\n- heading "首页"'


def test_snapshot_has_captcha_error():
    assert snapshot_has_captcha_error(_CAPTCHA_ERR_SNAP) is True
    assert snapshot_has_captcha_error(_OK_SNAP) is False
    assert snapshot_has_captcha_error("") is False


def test_extract_json_takes_first_of_multiple_objects():
    from app.services.ui_automation.ui_ai_explorer import _extract_json

    raw = (
        '{"action":"fill","selector":"e8","value":"admin"}\n'
        '{"action":"fill","selector":"e10","value":"secret"}\n'
        '{"action":"done","reason":"stop"}\n'
    )
    data = _extract_json(raw)
    assert data["action"] == "fill"
    assert data["selector"] == "e8"


def test_decide_parse_failure_continues_react_not_llm_error():
    """LLM decide throws → re-snapshot / continue, do not finish with llm_error."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- button "登录" [ref=e2]'
    fake_cli.cdp_endpoint = None
    calls = {"n": 0}

    def flaky_decide(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("下一步不是 JSON 对象")
        return {"action": "done", "reason": "recovered"}

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=flaky_decide,
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=8,
            session=fake_cli,
        )

    assert calls["n"] >= 2
    assert plan.get("_explore_finish_reason") != "llm_error"
    assert plan.get("_explore_finish_reason") in ("done", "", None) or plan.get(
        "_explore_finish_reason"
    ) == "done"


def test_captcha_retry_exception_continues_with_failure_nudge():
    fake_cli = MagicMock()
    fake_cli.snapshot.side_effect = [_CAPTCHA_ERR_SNAP, _OK_SNAP, _OK_SNAP]
    fake_cli.cdp_endpoint = None
    logs: list[str] = []

    with (
        patch(
            "app.services.ui_automation.ui_explore_graph.retry_captcha_after_failure",
            side_effect=RuntimeError("snapshot 中未找到验证码输入框 ref"),
        ),
        patch(
            "app.services.ui_automation.ui_explore_graph.decide_next_action",
            return_value={"action": "done", "reason": "ok"},
        ),
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=5,
            session=fake_cli,
            on_log=logs.append,
        )

    assert any("自动重试失败" in m for m in logs)
    assert plan.get("_explore_finish_reason") != CAPTCHA_NEED_HUMAN
    # Should complete via done, not abort as captcha_need_human on first fail
    assert plan.get("_explore_finish_reason") == "done"


def test_graph_open_snapshot_decide_done():
    cli = MagicMock()
    cli.snapshot.return_value = '- button "完成" [ref=e1]'
    cli.cdp_endpoint = None
    rt = ExploreRuntime(cli=cli, llm=MagicMock(), own_session=False)
    graph = build_ui_explore_graph(rt)

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        return_value={"action": "done", "reason": "ok"},
    ):
        final = graph.invoke(
            {
                "base_url": "https://example.com",
                "start_path": "/",
                "plan": {"goal": "打开首页", "start_path": "/"},
                "ctx": {},
                "url": "https://example.com/",
                "snapshot": "",
                "decision": {},
                "trace": [],
                "step_count": 0,
                "max_steps": 5,
                "finished": False,
                "cancelled": False,
                "error": "",
            }
        )
    cli.open.assert_called_once()
    cli.snapshot.assert_called()
    assert final.get("finished") is True
    assert any(t.get("action") == "goto" for t in (final.get("trace") or []))


def test_run_ui_explore_graph_click_then_done():
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- button "登录" [ref=e2]'
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("button", { name: "登录" })'
    decisions = [
        {"action": "click", "selector": "e2"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        trace = run_ui_explore_graph(
            base_url="https://example.com",
            plan={"goal": "点登录", "start_path": "/login"},
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=5,
            session=fake_cli,
        )
    fake_cli.open.assert_called_once()
    fake_cli.click.assert_called()
    kinds = [t.get("kind") or t.get("action") for t in trace]
    assert "goto" in kinds
    assert "click" in kinds


def test_captcha_fingerprint_buckets_different_wording():
    a = midscene_noop_fingerprint(
        {
            "driver": "midscene",
            "action": "click",
            "prompt": "读取登录页验证码图片中的字符, 只返回字符本身",
        }
    )
    b = midscene_noop_fingerprint(
        {
            "driver": "midscene",
            "action": "click",
            "prompt": "读取登录页验证码图片中显示的字符，只返回字符本身，不要点击任何控件",
        }
    )
    assert a == CAPTCHA_READ_FINGERPRINT
    assert a == b


def test_generic_midscene_repeated_prompt_stops_explore():
    """Same non-captcha midscene prompt twice → empty second act, then finish."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- button "图标" [ref=e1]'
    fake_cli.cdp_endpoint = "http://127.0.0.1:9222"
    fake_cli.timeout_ms = 5000
    decisions = [
        {"action": "click", "driver": "midscene", "prompt": "点击画布放大图标"},
        {"action": "click", "driver": "midscene", "prompt": "点击画布放大图标"},
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
        from app.services.ui_automation.ui_cli_explorer import REPEATED_MIDSCENE_NOOP

        plan: dict = {"goal": "点图标", "start_path": "/"}
        trace = run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=10,
            session=fake_cli,
            midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
        )

    assert mock_mid.call_count == 1
    assert any(t.get("explore_midscene_act") for t in trace)
    assert plan.get("_explore_finish_reason") == REPEATED_MIDSCENE_NOOP


def test_captcha_midscene_reads_once_then_fills_via_cli():
    """First captcha midscene → Midscene query + CLI fill; second wording skip."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = (
        '- textbox "验证码" [ref=e3]\n- textbox "请输入图中字符" [ref=e4]\n- button "登录" [ref=e5]'
    )
    fake_cli.cdp_endpoint = "http://127.0.0.1:9222"
    fake_cli.timeout_ms = 5000
    fake_cli.generate_locator.return_value = 'page.getByPlaceholder("请输入图中字符")'

    decisions = [
        {
            "action": "click",
            "driver": "midscene",
            "prompt": "读取登录页验证码图片中的字符, 只返回字符本身",
        },
        {
            "action": "click",
            "driver": "midscene",
            "prompt": "读取登录页验证码图片中显示的字符，不要点击任何控件",
        },
        {"action": "click", "selector": "e5", "driver": "playwright"},
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
            "app.services.ui_automation.node_ui_runner.midscene_query_over_cdp",
            return_value="Ab12",
        ) as mock_query,
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        trace = run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=10,
            session=fake_cli,
            midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
        )

    assert mock_query.call_count == 1
    fake_cli.fill.assert_called()
    fill_args = fake_cli.fill.call_args[0]
    assert fill_args[1] == "Ab12"
    mid_reads = [t for t in trace if t.get("explore_captcha_read")]
    fills = [t for t in trace if t.get("kind") == "fill_captcha"]
    assert len(mid_reads) == 1
    assert len(fills) == 1
    assert fake_cli.snapshot.call_count < 10


def test_captcha_error_auto_retries_then_continues():
    """First snapshot shows captcha error → auto refresh+fill once → decide done."""
    fake_cli = MagicMock()
    # take_snapshot: error snap, then post-retry snap; decide may not need more
    fake_cli.snapshot.side_effect = [_CAPTCHA_ERR_SNAP, _OK_SNAP, _OK_SNAP]
    fake_cli.cdp_endpoint = "http://127.0.0.1:9222"

    with (
        patch(
            "app.services.ui_automation.ui_explore_graph.retry_captcha_after_failure",
            return_value=[
                {"kind": "click", "action": "click", "reason": "captcha_retry_refresh", "status": "ok"},
                {"kind": "ai_query", "explore_captcha_read": True, "status": "ok"},
                {"kind": "fill_captcha", "status": "ok", "captcha": True},
            ],
        ) as mock_retry,
        patch(
            "app.services.ui_automation.ui_explore_graph.decide_next_action",
            return_value={"action": "done", "reason": "ok"},
        ),
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        trace = run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=5,
            session=fake_cli,
            midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
        )

    assert mock_retry.call_count == 1
    assert any(t.get("reason") == "captcha_retry_refresh" for t in trace)
    assert any(t.get("explore_captcha_read") for t in trace)


def test_captcha_error_three_retries_then_need_human():
    """Four error detections → 3 auto retries then on_need_human; reply 继续 → done."""
    fake_cli = MagicMock()
    # Each error take_snapshot: error + post-retry snap; 4th error then human then ok snap
    snaps = []
    for _ in range(3):
        snaps.extend([_CAPTCHA_ERR_SNAP, _OK_SNAP])  # retry path
    snaps.append(_CAPTCHA_ERR_SNAP)  # 4th fail → human
    snaps.append(_OK_SNAP)  # after human
    fake_cli.snapshot.side_effect = snaps
    fake_cli.cdp_endpoint = None
    # Avoid act-time snapshot fallback eating captcha side_effect order
    fake_cli.generate_locator.return_value = 'page.getByRole("button", { name: "登录" })'

    human_calls: list[str] = []

    def on_need_human(reason: str) -> str:
        human_calls.append(reason)
        return "继续"

    # After each successful retry nudge, force another login click that "fails" again
    # by returning click then eventually the next snapshot is error (via side_effect).
    # Simpler: open→snap(error)→retry→snap(ok)→decide(click)→act→snap(error)...
    # Vary selectors so click-streak guard does not skip the 3rd submit before 4th captcha fail.
    decisions = [
        {"action": "click", "selector": "e22", "name": "登录"},  # after retry 1
        {"action": "click", "selector": "e22", "name": "提交"},  # after retry 2
        {"action": "click", "selector": "e16", "name": "换一张"},  # after retry 3
        {"action": "done"},  # after human
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with (
        patch(
            "app.services.ui_automation.ui_explore_graph.retry_captcha_after_failure",
            return_value=[{"kind": "fill_captcha", "status": "ok", "captcha": True}],
        ) as mock_retry,
        patch(
            "app.services.ui_automation.ui_explore_graph.decide_next_action",
            side_effect=fake_decide,
        ),
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        trace = run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=20,
            session=fake_cli,
            on_need_human=on_need_human,
            midscene_model_config={"MIDSCENE_MODEL_NAME": "test"},
        )

    assert mock_retry.call_count == 3
    assert len(human_calls) == 1
    assert "3 次" in human_calls[0]
    assert any(t.get("action") == "goto" for t in trace)


def test_captcha_error_no_human_hook_finishes():
    """Without on_need_human, after 3 auto retries the 4th error ends explore."""
    fake_cli = MagicMock()
    snaps = []
    for _ in range(3):
        snaps.extend([_CAPTCHA_ERR_SNAP, _OK_SNAP])
    snaps.append(_CAPTCHA_ERR_SNAP)
    fake_cli.snapshot.side_effect = snaps
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("button", { name: "登录" })'

    decisions = [
        {"action": "click", "selector": "e22", "name": "登录"},
        {"action": "click", "selector": "e22", "name": "提交"},
        {"action": "click", "selector": "e16", "name": "换一张"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with (
        patch(
            "app.services.ui_automation.ui_explore_graph.retry_captcha_after_failure",
            return_value=[{"kind": "fill_captcha", "status": "ok", "captcha": True}],
        ),
        patch(
            "app.services.ui_automation.ui_explore_graph.decide_next_action",
            side_effect=fake_decide,
        ),
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=20,
            session=fake_cli,
            on_need_human=None,
        )

    assert plan.get("_explore_finish_reason") == CAPTCHA_NEED_HUMAN


def test_matrix_done_advances_to_next_case_same_session():
    """done on case 0 switches to case 1; browser stays open until all cases done."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- button "登录" [ref=e2]\n- button "新建" [ref=e3]'
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("button", { name: "x" })'
    goals_seen: list[str] = []
    decisions = [
        {"action": "click", "selector": "e2", "name": "登录"},
        {"action": "done", "reason": "login ok"},
        {"action": "click", "selector": "e3", "name": "新建"},
        {"action": "done", "reason": "create ok"},
    ]

    def fake_decide(**kwargs):
        plan = kwargs.get("plan") or {}
        goals_seen.append(str(plan.get("goal") or ""))
        active = plan.get("_active_case") if isinstance(plan.get("_active_case"), dict) else {}
        if active:
            goals_seen[-1] += f"|{active.get('id')}"
        return decisions.pop(0)

    plan: dict = {
        "goal": "登录与建项目",
        "start_path": "/login",
        "matrix": [
            {"id": "TC-01", "name": "登录成功", "expected": "首页", "start_url": "/login"},
            {"id": "TC-02", "name": "创建项目", "expected": "列表可见", "start_url": "/"},
        ],
    }
    logs: list[str] = []
    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        trace = run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=20,
            session=fake_cli,
            on_log=logs.append,
        )

    fake_cli.open.assert_called_once()
    fake_cli.close.assert_not_called()  # session owned by caller
    assert plan.get("_explore_finish_reason") == "done"
    assert any("切换到 TC-02" in m for m in logs)
    case_ids = {str(t.get("case_id") or "") for t in trace}
    assert "TC-01" in case_ids
    assert "TC-02" in case_ids
    assert any("TC-01" in g for g in goals_seen)
    assert any("TC-02" in g for g in goals_seen)
    assert not decisions  # all decisions consumed


def test_graph_calls_screenshot_on_open_and_act():
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- button "登录" [ref=e2]'
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("button", { name: "登录" })'
    fake_cli.screenshot.return_value = b"png"
    shots: list[dict] = []
    decisions = [
        {"action": "click", "selector": "e2"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        run_ui_explore_graph(
            base_url="https://example.com",
            plan={"goal": "点登录", "start_path": "/login"},
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=5,
            session=fake_cli,
            on_screenshot=lambda png, meta: shots.append(meta),
        )
    fake_cli.screenshot.assert_called()
    assert any(s.get("action") == "open" for s in shots)
    assert any(s.get("action") == "click" for s in shots)


def test_fill_streak_nudges_to_click_submit():
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- textbox "账号" [ref=e1]\n- button "登录" [ref=e2]'
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("textbox", { name: "账号" })'
    logs: list[str] = []
    decisions = [
        {"action": "fill", "selector": "e1", "value": "admin"},
        {"action": "fill", "selector": "e1", "value": "admin"},
        {"action": "fill", "selector": "e1", "value": "admin"},
        {"action": "click", "selector": "e2"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0)

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        run_ui_explore_graph(
            base_url="https://example.com",
            plan={"goal": "登录", "start_path": "/login"},
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=10,
            session=fake_cli,
            on_log=logs.append,
        )
    # clear-then-fill doubles each fill (empty + value); two acted fills → 4 calls
    # Two fills execute (clear+value each); 3rd triggers nudge skip_act (no hard click)
    assert fake_cli.fill.call_count == 4
    assert any("约束再决策" in m or ("连续" in m and "fill" in m) for m in logs)
    assert not any("改写为 click" in m for m in logs)


def test_snapshot_has_problem_hint_vs_captcha_placeholder():
    assert snapshot_has_problem_hint(_PROBLEM_SNAP) is True
    assert snapshot_has_problem_hint(_CAPTCHA_PLACEHOLDER_SNAP) is False
    assert snapshot_has_problem_hint(_OK_SNAP) is False
    assert snapshot_has_problem_hint("") is False


def test_assert_targets_problem_text():
    assert assert_targets_problem_text({"action": "assert_text", "value": "请输入项目名称"})
    assert assert_targets_problem_text(
        {"action": "assert_visible", "selector": "e178", "value": "请输入项目名称", "reason": "空名称校验"}
    )
    assert not assert_targets_problem_text({"action": "assert_text", "value": "创建成功"})
    assert not assert_targets_problem_text({"action": "assert_text", "value": "请输入图中字符"})


def test_force_fill_for_problem_hint_picks_title_textbox():
    snap = (
        '- dialog "新建"\n'
        '- textbox "标题" [ref=e200]\n'
        '- paragraph: 请输入标题 [ref=e333]\n'
        '- button "确定" [ref=e340]'
    )
    d = force_fill_for_problem_hint(
        snap,
        plan={"goal": "新建文章", "test_data": "标题：自动化标题"},
        ctx={},
        decision={"action": "assert_visible", "value": "请输入标题"},
    )
    assert d is not None
    assert d["action"] == "fill"
    assert d["selector"] == "e200"
    assert "自动化标题" in d["value"] or d["value"] == "自动化标题"
    assert d["reason"] == "problem_react_fill"


def test_problem_assert_nudges_agent_then_stops_without_hard_rewrite():
    """Problem assert → candidate nudge (no silent fill rewrite); repeat → stuck end."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = _PROBLEM_SNAP
    fake_cli.cdp_endpoint = None
    logs: list[str] = []
    decisions = [
        {"action": "assert_visible", "selector": "e178", "value": "请输入项目名称"},
        {"action": "assert_visible", "selector": "e178", "value": "请输入项目名称"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0) if decisions else {"action": "done"}

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        plan: dict = {"goal": "新建项目", "start_path": "/", "test_data": "项目名称：demo-project"}
        run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=12,
            session=fake_cli,
            on_log=logs.append,
        )
    assert fake_cli.fill.call_count == 0
    assert any("候选控件" in m or "再决策" in m or "禁止对问题" in m for m in logs)
    assert not any("改写为 fill" in m for m in logs)


def test_force_click_submit_picks_last_button_after_fields():
    """Structural pick: last button after textboxes (no label keyword matching)."""
    snap = (
        '- textbox "账号" [ref=e10]\n'
        '- textbox "密码" [ref=e11]\n'
        '- button "换一张验证码" [ref=e16]\n'
        '- button "登录" [ref=e22]'
    )
    d = force_click_submit(snap, filled_ref="e10")
    assert d is not None
    assert d["action"] == "click"
    assert d["selector"] == "e22"
    assert d["reason"] == "fill_streak_submit"
    assert force_click_submit('- textbox "账号" [ref=e1]') is None

    # Dialog scope: cancel then confirm → last button in dialog
    dialog_snap = (
        '- button "页面其它" [ref=e1]\n'
        '- dialog "新建项目"\n'
        '- textbox "项目名称" [ref=e164]\n'
        '- button "取消" [ref=e179]\n'
        '- button "确定" [ref=e180]'
    )
    d2 = force_click_submit(dialog_snap, filled_ref="e164")
    assert d2 is not None
    assert d2["selector"] == "e180"

    # Single button after fields
    d3 = force_click_submit('- button "唯一" [ref=e9]', filled_ref="")
    assert d3 is not None
    assert d3["selector"] == "e9"


def test_fill_streak_nudges_then_stuck_without_hard_click_rewrite():
    """Same fill 3x → candidate nudge (no silent click); 4th same fill → fill_streak_stuck."""
    login_snap = (
        '- textbox "账号" [ref=e10]\n'
        '- textbox "密码" [ref=e11]\n'
        '- button "登录" [ref=e22]'
    )
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = login_snap
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("textbox", { name: "账号" })'
    logs: list[str] = []
    decisions = [
        {"action": "fill", "selector": "e10", "value": "admin"},
        {"action": "fill", "selector": "e10", "value": "admin"},
        {"action": "fill", "selector": "e10", "value": "admin"},
        {"action": "fill", "selector": "e10", "value": "admin"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0) if decisions else {"action": "done"}

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        plan: dict = {"goal": "登录", "start_path": "/login"}
        run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={"username": "admin", "password": "x"},
            llm_cfg=MagicMock(),
            max_steps=10,
            session=fake_cli,
            on_log=logs.append,
        )
    assert not fake_cli.click.called
    assert any("约束再决策" in m for m in logs)
    assert not any("改写为 click" in m for m in logs)
    assert plan.get("_explore_finish_reason") == "fill_streak_stuck"


def test_fill_streak_with_problem_hint_nudges_not_hard_rewrite():
    """Problem-state same-field fill streak → nudge with candidates, no force click."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = _PROBLEM_SNAP
    fake_cli.cdp_endpoint = None
    fake_cli.generate_locator.return_value = 'page.getByRole("textbox", { name: "项目名称" })'
    logs: list[str] = []
    decisions = [
        {"action": "fill", "selector": "e164", "value": "x"},
        {"action": "fill", "selector": "e164", "value": "x"},
        {"action": "fill", "selector": "e164", "value": "x"},
        {"action": "done"},
    ]

    def fake_decide(**kwargs):
        return decisions.pop(0) if decisions else {"action": "done"}

    with patch(
        "app.services.ui_automation.ui_explore_graph.decide_next_action",
        side_effect=fake_decide,
    ):
        run_ui_explore_graph(
            base_url="https://example.com",
            plan={"goal": "新建项目", "start_path": "/"},
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=10,
            session=fake_cli,
            on_log=logs.append,
        )
    assert not fake_cli.click.called
    assert any("约束再决策" in m for m in logs)
    assert not any("改写为 click" in m for m in logs)


def test_run_ui_explore_graph_cancel_raises_manual_stop():
    """should_cancel must abort as MANUAL_STOP, not return a successful trace."""
    fake_cli = MagicMock()
    fake_cli.snapshot.return_value = '- button "登录" [ref=e2]'
    fake_cli.cdp_endpoint = None
    plan: dict = {"goal": "登录", "start_path": "/login"}

    with pytest.raises(RuntimeError, match=MANUAL_STOP_MESSAGE):
        run_ui_explore_graph(
            base_url="https://example.com",
            plan=plan,
            ctx={},
            llm_cfg=MagicMock(),
            max_steps=8,
            session=fake_cli,
            should_cancel=lambda: True,
        )
    assert plan.get("_explore_finish_reason") == "cancelled"
