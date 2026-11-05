"""Execution judgement must follow declared expectations, not "the agent said done"."""

from types import SimpleNamespace

from app.services.functional.case_expectation import (
    BLOCKED,
    FAILED,
    PASSED,
    case_expectations,
    judge_trace,
    texts_match,
)


def _case(*steps, overall=""):
    rows = [
        SimpleNamespace(order=i, action=action, expected=expected)
        for i, (action, expected) in enumerate(steps, start=1)
    ]
    return SimpleNamespace(steps=rows, overall_expected=overall)


def test_no_trace_is_failure():
    verdict = judge_trace([], _case(("打开登录页", "表单可见")))
    assert verdict.status == FAILED


def test_step_error_is_failure_even_with_done():
    verdict = judge_trace(
        [
            {"action": "click", "status": "ok"},
            {"action": "assert_text", "assert": "登录成功", "status": "error", "error": "文案不包含"},
            {"action": "done", "status": "ok"},
        ],
        _case(("登录", "登录成功")),
    )
    assert verdict.status == FAILED
    assert "文案不包含" in verdict.reason


def test_case_without_expectations_cannot_pass():
    verdict = judge_trace(
        [{"action": "goto", "status": "ok"}, {"action": "done", "status": "ok"}],
        _case(("打开页面", "—")),
    )
    assert verdict.status == BLOCKED
    assert "预期" in verdict.reason


def test_actions_without_assertions_are_blocked():
    verdict = judge_trace(
        [
            {"action": "goto", "status": "ok"},
            {"action": "fill", "selector": "#user", "status": "ok"},
            {"action": "done", "status": "ok"},
        ],
        _case(("输入账号", "表单可见"), overall="进入首页"),
    )
    assert verdict.status == BLOCKED
    assert "断言" in verdict.reason
    assert verdict.assertion_count == 0


def test_all_expectations_covered_passes():
    verdict = judge_trace(
        [
            {"action": "goto", "value": "/login", "status": "ok"},
            {"action": "assert_visible", "assert": "表单可见", "status": "ok"},
            {"action": "assert_text", "assert": "登录成功", "status": "ok"},
            {"action": "done", "status": "ok"},
        ],
        _case(("输入账号", "表单可见"), overall="登录成功"),
    )
    assert verdict.status == PASSED
    assert verdict.coverage == 1.0
    assert verdict.assertion_count == 2


def test_uncovered_must_test_step_blocks_pass():
    verdict = judge_trace(
        [
            {"action": "assert_text", "assert": "登录成功", "status": "ok"},
            {"action": "done", "status": "ok"},
        ],
        _case(("输入账号", "表单可见"), overall="登录成功"),
    )
    assert verdict.status == BLOCKED
    assert "未覆盖" in verdict.reason
    assert [p.order for p in verdict.uncovered] == [1]
    assert verdict.coverage == 0.5


def test_trace_without_done_is_failure():
    verdict = judge_trace(
        [{"action": "assert_text", "assert": "登录成功", "status": "ok"}],
        _case(("登录", "登录成功")),
    )
    assert verdict.status == FAILED


def test_unspecified_overall_and_placeholder_steps_are_ignored():
    points = case_expectations(_case(("打开", "—"), ("点击", "符合预期")))
    assert points == []


def test_expectation_matching_is_loose_but_not_blind():
    assert texts_match("登录成功后进入首页", "首页")
    assert texts_match("表单可见", "expect(#form).toBeVisible 表单可见")
    assert not texts_match("显示余额", "退出登录成功")
