"""Finalize functional TestCaseRun from UI scenario agent_once sessions."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.models.functional.testcase import TestCase
from app.models.functional.testcase_run import TestCaseRun
from app.models.ui_automation.ui_automation import UiScenarioAgentSession
from app.services.functional.case_expectation import Judgement, judge_trace
from app.services.functional.testcase_lifecycle import (
    case_snapshot,
    record_execution_result,
)

_MAX_STEPS = 80
_MAX_LOG_LINES = 200
_MAX_SHOTS = 50
_SHOT_STEP_RE = re.compile(r"已截图\s+step=(\d+)")
_SHOT_FILENAME_RE = re.compile(r"^(\d+)_[^/]+\.png$", re.IGNORECASE)

log = logging.getLogger(__name__)


def execution_mode_of(plan: dict[str, Any] | None) -> str:
    mode = str((plan or {}).get("_execution_mode") or "explore_codegen").strip()
    return mode if mode in ("explore_codegen", "agent_once") else "explore_codegen"


def case_run_id_of(plan: dict[str, Any] | None) -> int | None:
    raw = (plan or {}).get("_case_run_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def judge_agent_once_result(
    trace: list[Any] | None, *, case: TestCase | None = None
) -> tuple[str, str]:
    """Return (status, reason) for an agent_once trace.

    ``case`` carries the declared expectations. Without it the run can no longer
    be reported as passed: "the agent said done" is not evidence that the
    business expectation holds.
    """
    judgement = judge_trace(trace, case)
    return judgement.status, judgement.reason


def judge_agent_once(trace: list[Any] | None, case: TestCase | None) -> Judgement:
    """Full judgement (coverage / uncovered points) for storage and display."""
    return judge_trace(trace, case)


def _shot_key_from_item(item: dict[str, Any]) -> str:
    for key in ("screenshot_key", "screenshot"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    data = item.get("data")
    if isinstance(data, dict):
        raw = data.get("screenshot_key")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def snapshot_steps_from_trace(trace: list[Any] | None) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for i, raw in enumerate(trace or []):
        if len(steps) >= _MAX_STEPS:
            break
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip()
        if not action:
            continue
        step: dict[str, Any] = {
            "order": i + 1,
            "action": action,
            "selector": str(raw.get("selector") or raw.get("locate") or "")[:512],
            "value": str(raw.get("value") or "")[:512] if raw.get("value") is not None else "",
            "status": str(raw.get("status") or "ok")[:32],
            "error": str(raw.get("error") or "")[:1000],
        }
        shot = _shot_key_from_item(raw)
        if shot:
            step["screenshot_key"] = shot
        steps.append(step)
    return steps


def snapshot_log_lines_from_events(events: list[Any] | None) -> list[str]:
    lines: list[str] = []
    for ev in events or []:
        if len(lines) >= _MAX_LOG_LINES:
            break
        if not isinstance(ev, dict):
            continue
        msg = str(ev.get("message") or "").strip()
        if not msg:
            continue
        if msg.startswith("已截图") or "已截图 step=" in msg:
            continue
        lines.append(msg[:1000])
    return lines


def collect_screenshot_keys(
    *,
    explicit: list[str] | None,
    steps: list[dict[str, Any]],
    events: list[Any] | None,
) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        k = (key or "").strip()
        if not k or k in seen:
            return
        seen.add(k)
        keys.append(k)

    for k in explicit or []:
        add(str(k))
        if len(keys) >= _MAX_SHOTS:
            return keys
    for step in steps:
        add(str(step.get("screenshot_key") or ""))
        if len(keys) >= _MAX_SHOTS:
            return keys
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        add(_shot_key_from_item(ev))
        if len(keys) >= _MAX_SHOTS:
            return keys
    return keys


def _assign_step_screenshot(steps: list[dict[str, Any]], index: int, key: str) -> None:
    if not steps or not key:
        return
    idx = max(0, min(int(index), len(steps) - 1))
    step = steps[idx]
    if not isinstance(step, dict):
        return
    existing = step.get("screenshot_key")
    if isinstance(existing, str) and existing.strip():
        return
    step["screenshot_key"] = key


def _shot_index_from_key(key: str) -> int | None:
    base = key.replace("\\", "/").rsplit("/", 1)[-1]
    m = _SHOT_FILENAME_RE.match(base)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def attach_screenshots_to_steps(
    steps: list[dict[str, Any]],
    *,
    events: list[Any] | None = None,
    screenshot_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fill missing steps[].screenshot_key from explore events or filename keys."""
    if not steps:
        return steps

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        key = _shot_key_from_item(ev)
        if not key:
            continue
        msg = str(ev.get("message") or "")
        m = _SHOT_STEP_RE.search(msg)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        _assign_step_screenshot(steps, n, key)

    for raw in screenshot_keys or []:
        key = str(raw or "").strip()
        if not key:
            continue
        n = _shot_index_from_key(key)
        if n is None:
            continue
        _assign_step_screenshot(steps, n, key)

    return steps


def enrich_summary_step_screenshots(summary_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of summary_json with steps backfilled from screenshot_keys (read path)."""
    if not isinstance(summary_json, dict):
        return summary_json
    steps_raw = summary_json.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return summary_json
    steps = [dict(s) if isinstance(s, dict) else s for s in steps_raw]
    keys_raw = summary_json.get("screenshot_keys")
    keys = [str(k) for k in keys_raw] if isinstance(keys_raw, list) else None
    attach_screenshots_to_steps(steps, screenshot_keys=keys)
    return {**summary_json, "steps": steps}


def allowed_screenshot_keys(summary_json: dict[str, Any] | None) -> set[str]:
    data = summary_json if isinstance(summary_json, dict) else {}
    keys: set[str] = set()
    for raw in data.get("screenshot_keys") or []:
        if isinstance(raw, str) and raw.strip():
            keys.add(raw.strip())
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        shot = step.get("screenshot_key")
        if isinstance(shot, str) and shot.strip():
            keys.add(shot.strip())
    return keys


def build_run_summary_json(
    session: UiScenarioAgentSession,
    *,
    status: str,
    screenshot_keys: list[str] | None = None,
) -> dict[str, Any]:
    trace = session.trace_json if isinstance(session.trace_json, list) else []
    events = session.events_json if isinstance(session.events_json, list) else []
    steps = snapshot_steps_from_trace(trace)
    log_lines = snapshot_log_lines_from_events(events)
    shots = collect_screenshot_keys(explicit=screenshot_keys, steps=steps, events=events)
    attach_screenshots_to_steps(steps, events=events, screenshot_keys=shots)
    return {
        "result": status,
        "step_count": len(steps),
        "steps": steps,
        "log_lines": log_lines,
        "screenshot_keys": shots,
    }


def create_agent_once_run(
    db: Session,
    *,
    project_id: int,
    case_id: int,
    user_id: int | None,
    env_id: int | None,
    session_id: int,
    cycle_item_id: int | None = None,
) -> TestCaseRun:
    case = db.get(TestCase, case_id)
    run = TestCaseRun(
        project_id=project_id,
        case_id=case_id,
        triggered_by=user_id,
        mode="agent_once",
        status="running",
        env_id=env_id,
        session_id=session_id,
        cycle_item_id=cycle_item_id,
        summary="",
        error_message="",
        actual_result="",
        started_at=now_beijing(),
    )
    if case is not None and case.project_id == project_id:
        # Pin what was executed: later edits must not rewrite this evidence.
        run.summary_json = {
            "case_version_no": max(1, int(case.version_no or 1)),
            "case_snapshot": case_snapshot(case),
        }
    db.add(run)
    db.flush()
    return run


def finalize_agent_once(
    db: Session,
    session: UiScenarioAgentSession,
    *,
    status: str,
    error_message: str = "",
    summary: str = "",
    actual_result: str = "",
    screenshot_keys: list[str] | None = None,
    judgement: Judgement | None = None,
) -> TestCaseRun | None:
    """Update TestCaseRun + project the outcome onto TestCase.last_result.

    The case lifecycle is deliberately untouched: executing a case must not
    promote it to "confirmed" nor demote a confirmed case back to "failed".
    """
    plan = session.plan_json if isinstance(session.plan_json, dict) else {}
    if execution_mode_of(plan) != "agent_once":
        return None

    run_id = case_run_id_of(plan)
    run: TestCaseRun | None = None
    if run_id is not None:
        run = db.get(TestCaseRun, run_id)
    if run is None and session.testcase_id is not None:
        run = (
            db.query(TestCaseRun)
            .filter(
                TestCaseRun.session_id == session.id,
                TestCaseRun.project_id == session.project_id,
            )
            .order_by(TestCaseRun.id.desc())
            .first()
        )
    if run is None:
        return None

    final = status if status in ("passed", "failed", "blocked", "error", "cancelled") else "error"
    pinned = run.summary_json if isinstance(run.summary_json, dict) else {}
    run.status = final
    run.error_message = (error_message or "")[:4000]
    run.summary = (summary or "").strip() or (
        "Agent 直跑通过" if final == "passed" else f"Agent 直跑{final}"
    )
    run.actual_result = (actual_result or run.error_message or run.summary)[:8000]
    run.finished_at = now_beijing()
    if run.started_at is None:
        run.started_at = run.finished_at
    summary_json = build_run_summary_json(
        session, status=final, screenshot_keys=screenshot_keys
    )
    # Keep the case version/snapshot pinned when the run was created.
    if "case_version_no" in pinned:
        summary_json["case_version_no"] = pinned["case_version_no"]
    if isinstance(pinned.get("case_snapshot"), dict):
        summary_json["case_snapshot"] = pinned["case_snapshot"]
    if judgement is not None:
        summary_json["judgement"] = judgement.as_summary()
    run.summary_json = summary_json

    if session.testcase_id is not None:
        case = db.get(TestCase, session.testcase_id)
        if case is not None and case.project_id == session.project_id:
            record_execution_result(case, final, at=run.finished_at)

    if session.status not in ("cancelled",):
        # Keep error status on session when explore failed hard; otherwise done.
        if final in ("passed", "failed", "blocked") and session.status != "error":
            session.status = "done"
        elif final == "cancelled":
            session.status = "cancelled"
        elif final == "error" and session.status not in ("error", "cancelled"):
            session.status = "error"

    if isinstance(session.plan_json, dict):
        session.plan_json = {**session.plan_json, "_case_run_id": run.id, "_case_run_status": final}

    db.add(run)
    db.flush()
    # A run started from a batch item also records an attempt there. Failures are
    # logged, never allowed to break the run finalization itself.
    if run.cycle_item_id is not None:
        try:
            from app.services.functional.cycle_attempt_sync import sync_attempt_from_run

            sync_attempt_from_run(db, run_type="functional", run_id=run.id)
        except Exception:
            log.exception("sync functional attempt failed run_id=%s", run.id)
    return run
