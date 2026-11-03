"""LangGraph UI explore: prompt-driven decide + thin playwright-cli tools."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.ui_automation.playwright_cli_session import (
    PlaywrightCliSession,
    join_explore_url,
    unique_session_name,
)
from app.services.ui_automation.ui_ai_explorer import decide_next_action
from app.services.ui_automation.ui_cli_explorer import (
    CAPTCHA_MAX_AUTO_RETRIES,
    CAPTCHA_NEED_HUMAN,
    CAPTCHA_READ_FINGERPRINT,
    PROBLEM_REACT_NUDGE,
    REPEATED_MIDSCENE_NOOP,
    apply_cli_decision,
    assert_targets_problem_text,
    compile_cli_trace_script,
    format_cli_action_log,
    format_snapshot_action_candidates,
    last_trace_action_kind,
    midscene_noop_fingerprint,
    retry_captcha_after_failure,
    snapshot_has_captcha_error,
    snapshot_has_problem_hint,
    trace_has_midscene_fingerprint,
)

_LOG_MAX = 2000
_FILL_STREAK_LIMIT = 3
_CLICK_STREAK_LIMIT = 3
_ASSERT_STREAK_LIMIT = 2
_PROBLEM_ASSERT_REJECT_LIMIT = 2
_PROBLEM_TRIGGER_ACTIONS = frozenset(
    {"click", "assert_visible", "assert_text", "assert_url", "press"}
)
_PROBLEM_MUST_FILL_NUDGE = (
    "禁止 assert 校验/错误文案。下一步必须 Playwright fill 空必填输入框（用 test_data/goal），"
    "填完再提交；不要再次 assert_visible/assert_text。"
)
# Keep in sync with ui_scenario_agent / node_ui_runner MANUAL_STOP_MESSAGE
MANUAL_STOP_MESSAGE = "已手动停止探索"

log = logging.getLogger(__name__)


class UiExploreState(TypedDict, total=False):
    base_url: str
    start_path: str
    plan: dict[str, Any]
    ctx: dict[str, str]
    url: str
    snapshot: str
    decision: dict[str, Any]
    trace: list[dict[str, Any]]
    step_count: int
    max_steps: int
    finished: bool
    error: str
    cancelled: bool
    finish_reason: str
    midscene_nudge: str
    skip_act: bool  # recoverable decide failure → re-snapshot without acting
    last_click_key: str
    same_click_streak: int
    last_fill_key: str
    same_fill_streak: int
    last_assert_key: str
    same_assert_streak: int
    problem_assert_rejects: int
    active_case_index: int


def _matrix_rows(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [r for r in ((plan or {}).get("matrix") or []) if isinstance(r, dict)]


def _case_id_of(row: dict[str, Any], index: int) -> str:
    return str(row.get("id") or "").strip() or f"UC-{index + 1:03d}"


def _plan_for_case(plan: dict[str, Any], index: int) -> dict[str, Any]:
    """Focus decide_next_action on one matrix row; keep full matrix for context."""
    rows = _matrix_rows(plan)
    out = dict(plan or {})
    if not rows:
        out["_active_case_index"] = 0
        out.pop("_active_case", None)
        return out
    idx = max(0, min(index, len(rows) - 1))
    row = rows[idx]
    cid = _case_id_of(row, idx)
    name = str(row.get("name") or cid).strip()
    expected = str(row.get("expected") or "").strip()
    start = str(row.get("start_url") or out.get("start_path") or "/").strip() or "/"
    case_goal = f"完成用例 {cid}：{name}"
    if expected:
        case_goal += f"。预期：{expected}"
    out["_active_case_index"] = idx
    out["_active_case"] = {
        "id": cid,
        "name": name,
        "expected": expected,
        "start_url": start,
        "preconditions": row.get("preconditions"),
    }
    out["goal"] = case_goal
    remaining = [
        {"id": _case_id_of(r, i), "name": str(r.get("name") or "").strip()}
        for i, r in enumerate(rows)
        if i > idx
    ]
    out["_remaining_cases"] = remaining
    return out


def _active_case_id(state: UiExploreState) -> str:
    plan = dict(state.get("plan") or {})
    active = plan.get("_active_case")
    if isinstance(active, dict) and str(active.get("id") or "").strip():
        return str(active.get("id")).strip()
    idx = int(state.get("active_case_index") or plan.get("_active_case_index") or 0)
    rows = _matrix_rows(plan)
    if rows and 0 <= idx < len(rows):
        return _case_id_of(rows[idx], idx)
    return "UC-001"


def _tag_case(item: dict[str, Any], case_id: str) -> dict[str, Any]:
    if case_id and not item.get("case_id"):
        return {**item, "case_id": case_id}
    return item


@dataclass
class ExploreRuntime:
    """Mutable side channels (CLI session, LLM, callbacks) — not in LangGraph state."""

    cli: PlaywrightCliSession
    llm: LlmRuntime
    on_log: Callable[[str], None] | None = None
    on_step: Callable[[dict[str, Any]], None] | None = None
    on_need_human: Callable[[str], str | None] | None = None
    on_screenshot: Callable[[bytes, dict[str, Any]], str | None] | None = None
    should_cancel: Callable[[], bool] | None = None
    own_session: bool = True
    logs: list[str] = field(default_factory=list)
    midscene_model_config: dict[str, str] | None = None
    captcha_fail_count: int = 0


def snapshot_log_preview(snap: str, *, url: str = "", max_lines: int = 8) -> str:
    lines = [ln.strip() for ln in str(snap or "").splitlines() if ln.strip()]
    head = lines[:max_lines]
    extra = f" 等 {len(lines)} 行" if len(lines) > max_lines else ""
    body = " | ".join(head)
    loc = f"url={url} " if url else ""
    return f"LangGraph：snapshot {loc}{body}{extra}".strip()


def _log(rt: ExploreRuntime, message: str) -> None:
    text = str(message or "")[:_LOG_MAX]
    rt.logs.append(text)
    if rt.on_log:
        try:
            rt.on_log(text)
        except Exception:  # noqa: BLE001
            pass


def _screenshot(
    rt: ExploreRuntime,
    *,
    action: str,
    step: int,
    case_id: str = "",
    url: str = "",
) -> str | None:
    if not rt.on_screenshot:
        return None
    try:
        png = rt.cli.screenshot()
    except Exception as ex:  # noqa: BLE001
        _log(rt, f"截图失败: {ex}")
        return None
    if not png or not isinstance(png, (bytes, bytearray)):
        return None
    meta = {
        "action": action,
        "step": int(step),
        "case_id": case_id,
        "url": url,
    }
    try:
        key = rt.on_screenshot(bytes(png), meta)
        return str(key).strip() if isinstance(key, str) and key.strip() else None
    except Exception as ex:  # noqa: BLE001
        _log(rt, f"截图回调失败: {ex}")
        return None


def _note(rt: ExploreRuntime, item: dict[str, Any]) -> None:
    if rt.on_step:
        try:
            rt.on_step(item)
        except Exception:  # noqa: BLE001
            pass


def build_ui_explore_graph(rt: ExploreRuntime):
    """Compile open → snapshot → decide ⇄ act → END."""

    def open_page(state: UiExploreState) -> dict[str, Any]:
        if rt.should_cancel and rt.should_cancel():
            return {"cancelled": True, "finished": True}
        plan0 = _plan_for_case(dict(state.get("plan") or {}), 0)
        start = str(plan0.get("start_path") or state.get("start_path") or "/")
        active = plan0.get("_active_case") if isinstance(plan0.get("_active_case"), dict) else {}
        if active.get("start_url"):
            start = str(active.get("start_url") or start)
        base = str(state.get("base_url") or "")
        url = join_explore_url(base, start)
        case_id = _case_id_of(active, 0) if active else _active_case_id({"plan": plan0, "active_case_index": 0})
        rows = _matrix_rows(plan0)
        if len(rows) > 1:
            _log(rt, f"LangGraph：矩阵共 {len(rows)} 条用例，从 {case_id} 开始（同一浏览器会话）")
        _log(rt, f"LangGraph：playwright-cli 打开 {url}")
        rt.cli.open(url)
        item = _tag_case(
            {
                "kind": "goto",
                "action": "goto",
                "path": start,
                "value": start,
                "status": "ok",
                "driver": "playwright",
                "reason": "start_path",
            },
            case_id,
        )
        _note(rt, item)
        shot_key = _screenshot(rt, action="open", step=0, case_id=case_id, url=url)
        if shot_key:
            item["screenshot_key"] = shot_key
        return {
            "url": url,
            "plan": plan0,
            "active_case_index": 0,
            "trace": [item],
            "step_count": 0,
            "finished": False,
            "cancelled": False,
            "error": "",
        }

    def take_snapshot(state: UiExploreState) -> dict[str, Any]:
        if state.get("finished") or state.get("cancelled"):
            return {}
        if rt.should_cancel and rt.should_cancel():
            return {"cancelled": True, "finished": True}
        snap = rt.cli.snapshot()
        _log(rt, snapshot_log_preview(snap, url=str(state.get("url") or "")))
        if not snapshot_has_captcha_error(snap):
            return {"snapshot": snap[:14000]}

        rt.captcha_fail_count = int(rt.captcha_fail_count or 0) + 1
        n = rt.captcha_fail_count
        _log(rt, f"验证码失败（{n}/{CAPTCHA_MAX_AUTO_RETRIES}）")

        if n <= CAPTCHA_MAX_AUTO_RETRIES:
            retry_ok = False
            rows: list[dict[str, Any]] = []
            try:
                rows = retry_captcha_after_failure(
                    rt.cli,
                    midscene_model_config=rt.midscene_model_config,
                    on_log=lambda m: _log(rt, m),
                    snapshot=snap,
                )
                retry_ok = bool(rows)
            except Exception as ex:  # noqa: BLE001
                _log(rt, f"验证码自动重试失败: {ex}")
                rows = [
                    {
                        "action": "captcha_retry",
                        "kind": "captcha_retry",
                        "status": "error",
                        "error": str(ex)[:500],
                        "driver": "playwright",
                    }
                ]
            case_id = _active_case_id(state)
            trace = list(state.get("trace") or [])
            for row in rows:
                tagged = _tag_case(row, case_id)
                trace.append(tagged)
                _note(rt, tagged)
            try:
                snap = rt.cli.snapshot()
            except Exception:  # noqa: BLE001
                pass
            if retry_ok:
                nudge = (
                    "验证码已刷新并重填。下一步必须 Playwright click 页面上的提交/登录按钮；"
                    "禁止空点刷新控件或重复读图。"
                )
            else:
                nudge = (
                    "验证码自动重试失败。请用 midscene 读验证码图片并 Playwright fill 输入框，"
                    "再 click 提交/登录；不要假装已填、不要空点登录。"
                )
            return {
                "snapshot": snap[:14000],
                "trace": trace,
                "midscene_nudge": nudge,
                "error": "",
            }

        reason = (
            f"验证码连续失败 {CAPTCHA_MAX_AUTO_RETRIES} 次，"
            "请在浏览器中手动完成验证码/登录后发送「继续」"
        )
        if rt.on_need_human:
            _log(rt, reason)
            reply = None
            try:
                reply = rt.on_need_human(reason)
            except Exception as ex:  # noqa: BLE001
                _log(rt, f"人工协助回调失败: {ex}")
            if reply is None:
                return {
                    "snapshot": snap[:14000],
                    "finished": True,
                    "finish_reason": CAPTCHA_NEED_HUMAN,
                    "error": CAPTCHA_NEED_HUMAN,
                    "midscene_nudge": "",
                }
            rt.captcha_fail_count = 0
            _log(rt, "已收到人工协助，继续探索…")
            try:
                snap = rt.cli.snapshot()
            except Exception:  # noqa: BLE001
                pass
            return {
                "snapshot": snap[:14000],
                "midscene_nudge": (
                    "人工已处理验证码/登录。请从当前页继续目标；"
                    "若仍在登录表单且验证码已填，请 click 提交/登录按钮。"
                ),
            }

        _log(rt, f"{reason}（无头模式无法等待人工，结束探索）")
        return {
            "snapshot": snap[:14000],
            "finished": True,
            "finish_reason": CAPTCHA_NEED_HUMAN,
            "error": CAPTCHA_NEED_HUMAN,
            "midscene_nudge": "",
        }

    def decide(state: UiExploreState) -> dict[str, Any]:
        if state.get("finished") or state.get("cancelled"):
            return {"finished": True, "skip_act": False}
        if rt.should_cancel and rt.should_cancel():
            return {"cancelled": True, "finished": True, "skip_act": False}
        count = int(state.get("step_count") or 0)
        max_steps = max(1, int(state.get("max_steps") or 25))
        if count >= max_steps:
            _log(rt, "LangGraph：达到步数上限，结束探索")
            return {
                "finished": True,
                "finish_reason": "max_steps",
                "decision": {"action": "done", "reason": "max_steps"},
                "skip_act": False,
            }
        idx = int(state.get("active_case_index") or 0)
        plan = _plan_for_case(dict(state.get("plan") or {}), idx)
        case_id = _active_case_id({**state, "plan": plan, "active_case_index": idx})
        nudge = str(state.get("midscene_nudge") or "")
        snap = str(state.get("snapshot") or "")
        last_kind = last_trace_action_kind(list(state.get("trace") or []))
        # Problem state after click/assert → force react (not pure observation)
        if (
            not nudge
            and snapshot_has_problem_hint(snap)
            and last_kind in _PROBLEM_TRIGGER_ACTIONS
        ):
            nudge = PROBLEM_REACT_NUDGE
            _log(rt, "LangGraph：检测到问题/校验提示，注入纠偏 nudge")
        if nudge:
            plan = {**plan, "_explore_nudge": nudge}
        try:
            decision = decide_next_action(
                plan=plan,
                snapshot=str(state.get("snapshot") or ""),
                url=str(state.get("url") or ""),
                trace=list(state.get("trace") or []),
                ctx_keys=list((state.get("ctx") or {}).keys()),
                llm_cfg=rt.llm,
            )
        except Exception as ex:  # noqa: BLE001
            err = f"LLM 决策失败: {ex}"
            item = _tag_case(
                {
                    "action": "decide",
                    "kind": "decide",
                    "status": "error",
                    "error": err,
                    "driver": "playwright",
                },
                case_id,
            )
            trace = list(state.get("trace") or [])
            trace.append(item)
            _note(rt, item)
            _log(rt, f"{err}；继续探索，请只输出一个 JSON action")
            # Recoverable: re-snapshot / re-decide instead of closing the browser
            return {
                "finished": False,
                "skip_act": True,
                "error": "",
                "plan": plan,
                "trace": trace,
                "decision": {},
                "step_count": count + 1,
                "midscene_nudge": (
                    "上一步决策解析失败。请只输出一个 JSON 对象作为下一步 action，不要一次输出多个。"
                ),
            }
        if not str(decision.get("case_id") or "").strip():
            decision = {**decision, "case_id": case_id}
        action = str(decision.get("action") or "done").strip()
        _log(
            rt,
            "LangGraph：decide "
            + format_cli_action_log(action, decision, ctx=dict(state.get("ctx") or {})),
        )
        if action == "done":
            item = _tag_case(
                {**decision, "kind": "done", "status": "ok", "driver": "playwright"},
                case_id,
            )
            trace = list(state.get("trace") or [])
            trace.append(item)
            _note(rt, item)
            rows = _matrix_rows(plan)
            if len(rows) > 1 and idx + 1 < len(rows):
                next_idx = idx + 1
                next_plan = _plan_for_case(plan, next_idx)
                next_id = _case_id_of(rows[next_idx], next_idx)
                next_name = str(rows[next_idx].get("name") or next_id).strip()
                _log(rt, f"LangGraph：用例 {case_id} 完成，切换到 {next_id}（不关闭浏览器）")
                return {
                    "finished": False,
                    "finish_reason": "",
                    "decision": {},
                    "plan": next_plan,
                    "active_case_index": next_idx,
                    "trace": trace,
                    "step_count": count + 1,
                    "skip_act": True,
                    "same_click_streak": 0,
                    "last_click_key": "",
                    "same_fill_streak": 0,
                    "last_fill_key": "",
                    "same_assert_streak": 0,
                    "last_assert_key": "",
                    "problem_assert_rejects": 0,
                    "midscene_nudge": (
                        f"用例 {case_id} 已完成。请从当前页面继续完成下一用例 {next_id}：{next_name}。"
                        "不要重新打开站点或重复登录，除非当前已退回登录页。"
                    ),
                }
            _log(rt, "LangGraph：目标完成 (done)" + (f"；矩阵 {len(rows)} 条均已覆盖" if len(rows) > 1 else ""))
            return {
                "finished": True,
                "finish_reason": "done",
                "decision": decision,
                "plan": plan,
                "trace": trace,
                "step_count": count + 1,
                "skip_act": False,
            }
        # Stuck-click / stuck-fill / stuck-assert guard → change course
        click_key = ""
        if action == "click":
            click_key = str(
                decision.get("selector")
                or decision.get("name")
                or decision.get("locate")
                or ""
            ).strip()
        streak = int(state.get("same_click_streak") or 0)
        last_key = str(state.get("last_click_key") or "")
        if action == "click" and click_key and click_key == last_key:
            streak += 1
        elif action == "click" and click_key:
            streak = 1
        else:
            streak = 0
        fill_key = ""
        if action == "fill":
            fill_key = str(
                decision.get("selector")
                or decision.get("placeholder")
                or decision.get("name")
                or decision.get("locate")
                or ""
            ).strip()
        fill_streak = int(state.get("same_fill_streak") or 0)
        last_fill = str(state.get("last_fill_key") or "")
        if action == "fill" and fill_key and fill_key == last_fill:
            fill_streak += 1
        elif action == "fill" and fill_key:
            fill_streak = 1
        else:
            fill_streak = 0
        assert_key = ""
        if action in ("assert_visible", "assert_text"):
            assert_key = str(
                decision.get("selector")
                or decision.get("value")
                or decision.get("assert")
                or decision.get("name")
                or ""
            ).strip()
        assert_streak = int(state.get("same_assert_streak") or 0)
        last_assert = str(state.get("last_assert_key") or "")
        if action in ("assert_visible", "assert_text") and assert_key and assert_key == last_assert:
            assert_streak += 1
        elif action in ("assert_visible", "assert_text") and assert_key:
            assert_streak = 1
        else:
            assert_streak = 0
        problem_snap = snapshot_has_problem_hint(str(state.get("snapshot") or ""))
        stuck_nudge = ""
        problem_rejects = int(state.get("problem_assert_rejects") or 0)
        snap_text = str(state.get("snapshot") or "")
        candidates = format_snapshot_action_candidates(snap_text)
        prior_nudge = str(state.get("midscene_nudge") or "")
        # Agent-first: never silently rewrite decisions. Nudge with candidates; repeat → stop.
        if action in ("assert_visible", "assert_text") and assert_targets_problem_text(decision):
            problem_rejects += 1
            if problem_rejects >= _PROBLEM_ASSERT_REJECT_LIMIT:
                _log(rt, "LangGraph：问题 assert 连续拒绝，结束本轮纠偏空转")
                return {
                    "finished": True,
                    "finish_reason": "problem_assert_loop",
                    "decision": {
                        "action": "done",
                        "reason": "problem_assert_loop",
                        "status": "error",
                    },
                    "error": "problem_assert_loop",
                    "step_count": count + 1,
                    "skip_act": False,
                    "problem_assert_rejects": problem_rejects,
                    "midscene_nudge": "",
                }
            stuck_nudge = _PROBLEM_MUST_FILL_NUDGE
            if candidates:
                stuck_nudge = f"{stuck_nudge}\n{candidates}"
            _log(rt, "LangGraph：禁止对问题/校验文案 assert，注入候选控件 nudge（由 Agent 再决策）")
        elif assert_streak >= _ASSERT_STREAK_LIMIT:
            if "连续" in prior_nudge and "assert" in prior_nudge and assert_streak > _ASSERT_STREAK_LIMIT:
                _log(rt, f"LangGraph：连续 {assert_streak} 次相同 assert 且忽略 nudge，结束探索")
                return {
                    "finished": True,
                    "finish_reason": "assert_streak_stuck",
                    "decision": {"action": "done", "reason": "assert_streak_stuck", "status": "error"},
                    "error": "assert_streak_stuck",
                    "step_count": count + 1,
                    "skip_act": False,
                    "midscene_nudge": "",
                }
            stuck_nudge = PROBLEM_REACT_NUDGE
            if candidates:
                stuck_nudge = f"{stuck_nudge}\n{candidates}"
            _log(rt, f"LangGraph：连续 {assert_streak} 次相同 assert（{assert_key}），约束再决策")
        elif fill_streak >= _FILL_STREAK_LIMIT:
            if fill_streak > _FILL_STREAK_LIMIT and (
                "相同 fill" in prior_nudge or "连续多次 fill" in prior_nudge or "候选控件" in prior_nudge
            ):
                _log(rt, f"LangGraph：连续 {fill_streak} 次相同 fill 且忽略 nudge，结束探索")
                return {
                    "finished": True,
                    "finish_reason": "fill_streak_stuck",
                    "decision": {
                        "action": "done",
                        "reason": "fill_streak_stuck",
                        "status": "error",
                    },
                    "error": "fill_streak_stuck",
                    "step_count": count + 1,
                    "skip_act": False,
                    "midscene_nudge": "",
                    "same_fill_streak": fill_streak,
                    "last_fill_key": fill_key,
                }
            if problem_snap:
                stuck_nudge = (
                    "已连续多次 fill 同一输入框，且页面仍有校验/问题提示。"
                    "请根据候选控件改填其它框或 click 提交类按钮；禁止再 fill 同一 selector。"
                )
            else:
                stuck_nudge = (
                    "已连续多次 fill 同一输入框且未前进。"
                    "下一步必须从候选控件中选一个不同动作（通常 click 表单末按钮）；"
                    "禁止再 fill 同一 selector。"
                )
            if candidates:
                stuck_nudge = f"{stuck_nudge}\n{candidates}"
            _log(
                rt,
                f"LangGraph：连续 {fill_streak} 次相同 fill（{fill_key}），约束再决策（不硬改写）",
            )
        elif streak >= _CLICK_STREAK_LIMIT:
            if streak > _CLICK_STREAK_LIMIT and ("相同 click" in prior_nudge or "候选控件" in prior_nudge):
                _log(rt, f"LangGraph：连续 {streak} 次相同 click 且忽略 nudge，结束探索")
                return {
                    "finished": True,
                    "finish_reason": "click_streak_stuck",
                    "decision": {"action": "done", "reason": "click_streak_stuck", "status": "error"},
                    "error": "click_streak_stuck",
                    "step_count": count + 1,
                    "skip_act": False,
                    "midscene_nudge": "",
                }
            if problem_snap:
                stuck_nudge = PROBLEM_REACT_NUDGE
            else:
                stuck_nudge = (
                    "已连续多次点击同一控件仍未前进。"
                    "请换候选中的其它控件，或 midscene 读验证码后 fill；禁止继续空点同一按钮。"
                )
            if candidates:
                stuck_nudge = f"{stuck_nudge}\n{candidates}"
            _log(rt, f"LangGraph：连续 {streak} 次相同 click（{click_key}），约束再决策")
        fp = midscene_noop_fingerprint(decision)
        if fp and trace_has_midscene_fingerprint(list(state.get("trace") or []), fp):
            captcha_done = fp == CAPTCHA_READ_FINGERPRINT and any(
                isinstance(t, dict)
                and (
                    t.get("explore_captcha_read")
                    or t.get("kind") == "fill_captcha"
                    or t.get("captcha")
                )
                and t.get("status") != "error"
                for t in (state.get("trace") or [])
            )
            # Already read+filled captcha: stop re-query; allow one nudge then finish cleanly.
            if fp == CAPTCHA_READ_FINGERPRINT and captcha_done and nudge:
                _log(rt, "LangGraph：验证码已识别并填写，停止重复读图")
                item = _tag_case(
                    {
                        "action": "done",
                        "kind": "done",
                        "status": "ok",
                        "driver": "playwright",
                        "reason": "captcha_already_filled",
                    },
                    case_id,
                )
                trace = list(state.get("trace") or [])
                trace.append(item)
                _note(rt, item)
                return {
                    "finished": True,
                    "finish_reason": "captcha_already_filled",
                    "decision": item,
                    "plan": plan,
                    "trace": trace,
                    "step_count": count + 1,
                    "skip_act": False,
                }
            if nudge and fp != CAPTCHA_READ_FINGERPRINT:
                _log(rt, "LangGraph：同一 midscene 步空转，结束探索")
                return {
                    "finished": True,
                    "finish_reason": REPEATED_MIDSCENE_NOOP,
                    "decision": {**decision, "action": "done", "reason": REPEATED_MIDSCENE_NOOP},
                    "error": REPEATED_MIDSCENE_NOOP,
                    "step_count": count + 1,
                    "skip_act": False,
                }
            decision = {**decision, "_trace_has_same": True}
        out: dict[str, Any] = {
            "decision": decision,
            "plan": plan,
            "active_case_index": idx,
            "step_count": count + 1,
            "finished": False,
            "skip_act": False,
            "error": "",
            "last_click_key": click_key if action == "click" else "",
            "same_click_streak": streak if action == "click" else 0,
            "last_fill_key": fill_key if action == "fill" else "",
            "same_fill_streak": fill_streak if action == "fill" else 0,
            "last_assert_key": assert_key if action in ("assert_visible", "assert_text") else "",
            "same_assert_streak": assert_streak if action in ("assert_visible", "assert_text") else 0,
            "problem_assert_rejects": problem_rejects,
        }
        if stuck_nudge:
            out["midscene_nudge"] = stuck_nudge
            out["skip_act"] = True  # re-decide with stuck nudge instead of another empty click
            out["decision"] = {}
        return out

    def act(state: UiExploreState) -> dict[str, Any]:
        if state.get("finished") or state.get("cancelled"):
            return {}
        if rt.should_cancel and rt.should_cancel():
            return {"cancelled": True, "finished": True}
        decision = dict(state.get("decision") or {})
        case_id = _active_case_id(state)
        if not str(decision.get("case_id") or "").strip():
            decision = {**decision, "case_id": case_id}
        trace = list(state.get("trace") or [])
        url = str(state.get("url") or "")
        fp = midscene_noop_fingerprint(decision)
        already = bool(fp and trace_has_midscene_fingerprint(trace, fp))
        if already:
            decision = {**decision, "_trace_has_same": True}
        try:
            rows, url = apply_cli_decision(
                rt.cli,
                decision,
                base_url=str(state.get("base_url") or ""),
                ctx=dict(state.get("ctx") or {}),
                url=url,
                on_log=lambda m: _log(rt, m),
                midscene_model_config=rt.midscene_model_config,
            )
            for row in rows:
                tagged = _tag_case(row, case_id)
                trace.append(tagged)
                _note(rt, tagged)
        except Exception as ex:  # noqa: BLE001
            item = _tag_case(
                {
                    **decision,
                    "kind": str(decision.get("action") or "act"),
                    "status": "error",
                    "error": str(ex)[:500],
                    "driver": "playwright",
                },
                case_id,
            )
            trace.append(item)
            _note(rt, item)
            _log(rt, f"LangGraph：操作失败 {ex}；继续探索")
            return {
                "trace": trace,
                "url": url,
                "error": "",
                "midscene_nudge": (
                    f"上一步操作失败（{str(ex)[:120]}）。请根据最新 snapshot 换 locator 或改用 midscene，继续目标。"
                ),
            }
        act_name = str(decision.get("action") or "act").strip() or "act"
        shot_key = _screenshot(
            rt,
            action=act_name,
            step=int(state.get("step_count") or 0),
            case_id=case_id,
            url=url,
        )
        if shot_key and trace:
            trace[-1]["screenshot_key"] = shot_key
        out: dict[str, Any] = {"trace": trace, "url": url, "error": ""}
        if already or (fp and not rows):
            if fp == CAPTCHA_READ_FINGERPRINT:
                out["midscene_nudge"] = (
                    "验证码已识别并填入输入框。下一步必须 Playwright click 页面上的提交/登录按钮；"
                    "禁止再输出 midscene 读验证码。"
                )
            else:
                out["midscene_nudge"] = (
                    "同一 midscene 步已执行过。请改用 Playwright 或换不同 prompt；勿重复。"
                )
                if already and not rows:
                    out["finished"] = True
                    out["finish_reason"] = REPEATED_MIDSCENE_NOOP
                    out["error"] = REPEATED_MIDSCENE_NOOP
                    _log(rt, "LangGraph：同一 midscene 步空转，结束探索")
        elif rows and any(r.get("explore_captcha_read") for r in rows):
            out["midscene_nudge"] = (
                "验证码已识别并填入。下一步请 click 提交/登录按钮（Playwright），勿再读图。"
            )
        elif rows and any(r.get("explore_midscene_act") for r in rows):
            out["midscene_nudge"] = (
                "上一步 Midscene 视觉操作已执行。请继续推进 goal；"
                "有稳定 ref 时优先 Playwright，勿重复同一 midscene prompt。"
            )
        elif rows and any(r.get("explore_noop") for r in rows):
            out["midscene_nudge"] = (
                "上一步 Midscene 未能执行（缺 CDP 或模型配置）。"
                "请改用 Playwright 操作，或检查 Midscene 配置后重试。"
            )
        elif rows and any(
            r.get("driver") == "midscene" and r.get("status") == "error" for r in rows
        ):
            out["midscene_nudge"] = (
                "上一步 Midscene 执行失败。请改用 Playwright locator，或换更明确的 prompt。"
            )
        elif rows and any(r.get("explore_problem_assert_skip") for r in rows):
            out["midscene_nudge"] = PROBLEM_REACT_NUDGE
            _log(rt, "LangGraph：跳过问题文案 assert，注入纠偏 nudge")
        return out

    def route_after_decide(state: UiExploreState) -> Literal["act", "snapshot", "__end__"]:
        # Only hard stops — do not END on soft/recoverable state.error
        if state.get("finished") or state.get("cancelled"):
            return END  # type: ignore[return-value]
        if state.get("finish_reason") in (REPEATED_MIDSCENE_NOOP, CAPTCHA_NEED_HUMAN):
            return END  # type: ignore[return-value]
        if state.get("skip_act"):
            return "snapshot"
        action = str((state.get("decision") or {}).get("action") or "done").strip()
        if action == "done" or not action:
            if state.get("finished"):
                return END  # type: ignore[return-value]
            # Empty decision after recoverable failure → re-snapshot
            if not (state.get("decision") or {}):
                return "snapshot"
            return END  # type: ignore[return-value]
        return "act"

    def route_after_act(state: UiExploreState) -> Literal["snapshot", "__end__"]:
        if state.get("finished") or state.get("cancelled"):
            return END  # type: ignore[return-value]
        if state.get("finish_reason") == REPEATED_MIDSCENE_NOOP:
            return END  # type: ignore[return-value]
        # Cancel is stamped on state in act/snapshot/decide; also bail if flag flipped mid-route
        if rt.should_cancel and rt.should_cancel():
            return END  # type: ignore[return-value]
        count = int(state.get("step_count") or 0)
        max_steps = max(1, int(state.get("max_steps") or 25))
        if count >= max_steps:
            return END  # type: ignore[return-value]
        return "snapshot"

    g = StateGraph(UiExploreState)
    g.add_node("open", open_page)
    g.add_node("snapshot", take_snapshot)
    g.add_node("decide", decide)
    g.add_node("act", act)
    g.add_edge(START, "open")
    g.add_edge("open", "snapshot")
    g.add_edge("snapshot", "decide")
    g.add_conditional_edges(
        "decide",
        route_after_decide,
        {"act": "act", "snapshot": "snapshot", END: END},
    )
    g.add_conditional_edges("act", route_after_act, {"snapshot": "snapshot", END: END})
    return g.compile()


def run_ui_explore_graph(
    *,
    base_url: str,
    plan: dict[str, Any],
    ctx: dict[str, str],
    llm_cfg: LlmRuntime,
    headed: bool = False,
    max_steps: int = 25,
    timeout_ms: int = 15000,
    should_cancel: Callable[[], bool] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_need_human: Callable[[str], str | None] | None = None,
    on_screenshot: Callable[[bytes, dict[str, Any]], str | None] | None = None,
    session: PlaywrightCliSession | None = None,
    midscene_model_config: dict[str, str] | None = None,
    storage_state: dict[str, Any] | str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Run LangGraph explore; returns trace (no done rows unless error).

    Midscene steps run over CDP (aiTap/aiInput/aiQuery). Captcha is a recipe:
    aiQuery then playwright-cli fill. Captcha invalid → auto retry ≤3, then on_need_human.
    """
    own = session is None
    cli = session or PlaywrightCliSession(
        headed=headed,
        session_name=unique_session_name(),
        timeout_ms=timeout_ms,
        use_cdp=True,
        storage_state=storage_state,
        extra_http_headers=extra_http_headers,
        local_storage=local_storage,
    )
    rt = ExploreRuntime(
        cli=cli,
        llm=llm_cfg,
        on_log=on_log,
        on_step=on_step,
        on_need_human=on_need_human,
        on_screenshot=on_screenshot,
        should_cancel=should_cancel,
        own_session=own,
        midscene_model_config=midscene_model_config,
    )
    graph = build_ui_explore_graph(rt)
    plan0 = _plan_for_case(dict(plan or {}), 0)
    start = str(plan0.get("start_path") or "/")
    active = plan0.get("_active_case") if isinstance(plan0.get("_active_case"), dict) else {}
    if active.get("start_url"):
        start = str(active.get("start_url") or start)
    init: UiExploreState = {
        "base_url": base_url,
        "start_path": start,
        "plan": plan0,
        "ctx": ctx,
        "url": join_explore_url(base_url, start),
        "snapshot": "",
        "decision": {},
        "trace": [],
        "step_count": 0,
        "max_steps": max(1, int(max_steps)),
        "finished": False,
        "cancelled": False,
        "error": "",
        "finish_reason": "",
        "midscene_nudge": "",
        "active_case_index": 0,
    }
    final: dict[str, Any] | None = None
    try:
        final = graph.invoke(init)
        if own:
            captured = cli.dump_storage_state()
            if captured and isinstance(plan, dict):
                plan["_captured_storage_state"] = captured
    finally:
        if own:
            cli.close()
    cancelled = bool((final or {}).get("cancelled"))
    if not cancelled and should_cancel is not None and should_cancel():
        cancelled = True
    if cancelled:
        if isinstance(plan, dict):
            plan["_explore_finish_reason"] = "cancelled"
        raise RuntimeError(MANUAL_STOP_MESSAGE)
    finish_reason = str((final or {}).get("finish_reason") or "")
    if isinstance(plan, dict) and finish_reason:
        plan["_explore_finish_reason"] = finish_reason
    trace = list((final or {}).get("trace") or [])
    return [t for t in trace if t.get("action") != "done" or t.get("status") == "error"]


def run_ui_explore_graph_with_script(
    *,
    base_url: str,
    start_path: str,
    plan: dict[str, Any],
    username: str,
    password: str,
    llm_cfg: LlmRuntime,
    headed: bool = False,
    max_steps: int = 25,
    timeout_ms: int = 15000,
    should_cancel: Callable[[], bool] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    plan_obj = dict(plan or {})
    plan_obj.setdefault("start_path", start_path or "/")
    trace = run_ui_explore_graph(
        base_url=base_url,
        plan=plan_obj,
        ctx={"username": username, "password": password},
        llm_cfg=llm_cfg,
        headed=headed,
        max_steps=max_steps,
        timeout_ms=timeout_ms,
        should_cancel=should_cancel,
        on_step=on_step,
        on_log=on_log,
    )
    script = compile_cli_trace_script(
        trace,
        start_path=start_path or "/",
        username=username,
        password=password,
    )
    return trace, script
