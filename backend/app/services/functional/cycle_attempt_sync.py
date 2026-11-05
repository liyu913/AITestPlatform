"""Mirror finished automation runs into their batch item's attempt history.

A batch item owns the evidence: when an API/UI/functional run finishes, the
result is written as a :class:`TestCycleAttempt` and projected onto the item.
The upsert is keyed by ``(cycle_item_id, original_run_type, original_run_id)``,
so a duplicated callback updates the same attempt instead of appending a second
one, and a first terminal result is never silently overwritten by a stale
running callback.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.models.api_automation.api_automation import ApiRun
from app.models.functional.test_plan import TestCycle, TestCycleAttempt, TestCycleItem, TestPlan
from app.models.functional.testcase_run import TestCaseRun
from app.models.ui_automation.ui_automation import UiRun

log = logging.getLogger(__name__)

RUN_MODELS: dict[str, type] = {
    "functional": TestCaseRun,
    "api": ApiRun,
    "ui": UiRun,
}

EXECUTION_TYPE: dict[str, str] = {
    "functional": "agent_once",
    "api": "api",
    "ui": "ui",
}

# run.status → attempt.status (identical vocabulary, kept explicit).
ATTEMPT_STATUS = {
    "pending": "running",
    "running": "running",
    "passed": "passed",
    "failed": "failed",
    "blocked": "blocked",
    "error": "error",
    "cancelled": "cancelled",
}

ITEM_RESULT = {
    "passed": "passed",
    "failed": "failed",
    "blocked": "blocked",
    "error": "failed",
    "cancelled": "untested",
}

_TERMINAL = {"passed", "failed", "blocked", "error", "cancelled"}


def _compact_summary(run: Any) -> str:
    summary = getattr(run, "summary_json", None)
    if isinstance(summary, dict):
        parts: list[str] = []
        for key in ("passed", "failed", "error", "total", "message", "summary"):
            value = summary.get(key)
            if value not in (None, "", []):
                parts.append(f"{key}={value}")
        if parts:
            return " ".join(parts)[:2000]
    for attr in ("summary", "error_message"):
        text = str(getattr(run, attr, "") or "").strip()
        if text:
            return text[:2000]
    return ""


def _compact_evidence(run: Any, run_type: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {"run_type": run_type, "run_id": int(run.id), "status": str(run.status)}
    summary = getattr(run, "summary_json", None)
    if isinstance(summary, dict):
        keys = summary.get("screenshot_keys")
        if isinstance(keys, list):
            evidence["screenshot_keys"] = [str(k) for k in keys[:50]]
        steps = summary.get("steps")
        if isinstance(steps, list):
            evidence["step_count"] = len(steps)
        if isinstance(summary.get("step_count"), int):
            evidence["step_count"] = summary["step_count"]
    error = str(getattr(run, "error_message", "") or "").strip()
    if error:
        evidence["error_message"] = error[:1000]
    return evidence


def validate_cycle_item(db: Session, project_id: int, cycle_item_id: int | None) -> TestCycleItem | None:
    """Load a batch item that belongs to the project, else reject."""
    if cycle_item_id is None:
        return None
    row = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycleItem.id == cycle_item_id, TestPlan.project_id == project_id)
        .first()
    )
    if row is None:
        from app.errors import AppError

        raise AppError("bad_request", "执行项不存在或不属于当前项目", status_code=400)
    cycle = db.get(TestCycle, row.cycle_id)
    # Legacy batches were created as active before the readiness gate existed.
    if cycle is None or (cycle.started_at is None and cycle.status != "active"):
        from app.errors import AppError

        raise AppError(
            "conflict",
            "批次尚未开始执行，请先在批次详情完成就绪检查并开始",
            status_code=409,
        )
    return row


def _find_attempt(
    db: Session, cycle_item_id: int, run_type: str, run_id: int
) -> TestCycleAttempt | None:
    return (
        db.query(TestCycleAttempt)
        .filter(
            TestCycleAttempt.cycle_item_id == cycle_item_id,
            TestCycleAttempt.original_run_type == run_type,
            TestCycleAttempt.original_run_id == run_id,
        )
        .first()
    )


def _next_attempt_no(db: Session, cycle_item_id: int) -> int:
    last = (
        db.query(TestCycleAttempt.attempt_no)
        .filter(TestCycleAttempt.cycle_item_id == cycle_item_id)
        .order_by(TestCycleAttempt.attempt_no.desc())
        .first()
    )
    return int(last[0] or 0) + 1 if last else 1


def sync_attempt_quietly(db: Session, *, run_type: str, run_id: int) -> None:
    """Sync for request paths where a bookkeeping failure must not fail the run."""
    try:
        sync_attempt_from_run(db, run_type=run_type, run_id=run_id)
    except Exception:
        log.exception("sync %s attempt failed run_id=%s", run_type, run_id)


def _project_onto_item(
    db: Session, item: TestCycleItem, *, status: str, run_type: str, run_id: int, summary: str, at: Any
) -> None:
    item.last_run_type = run_type
    item.last_run_id = run_id
    mapped = ITEM_RESULT.get(status)
    if mapped is None:
        return
    item.result = mapped
    if summary:
        item.actual_result = summary[:65535]
    item.executed_at = at
    if item.case_id is not None:
        from app.models.functional.testcase import TestCase
        from app.services.functional.testcase_lifecycle import record_execution_result

        case = db.get(TestCase, item.case_id)
        cycle = db.get(TestCycle, item.cycle_id)
        plan = db.get(TestPlan, cycle.plan_id) if cycle is not None else None
        if case is not None and plan is not None and case.project_id == plan.project_id:
            record_execution_result(case, mapped, at=at)


def sync_attempt_from_run(db: Session, *, run_type: str, run_id: int) -> TestCycleAttempt | None:
    """Create or update the attempt that mirrors ``run``; no-op without a batch item."""
    model = RUN_MODELS.get(run_type)
    if model is None:
        log.warning("sync_attempt_from_run: unknown run type %s", run_type)
        return None
    run = db.get(model, run_id)
    if run is None or getattr(run, "cycle_item_id", None) is None:
        return None
    item = db.get(TestCycleItem, int(run.cycle_item_id))
    if item is None:
        return None

    status = ATTEMPT_STATUS.get(str(run.status), "running")
    summary = _compact_summary(run)
    evidence = _compact_evidence(run, run_type)
    now = now_beijing()
    attempt = _find_attempt(db, item.id, run_type, run_id)
    if attempt is None:
        attempt = TestCycleAttempt(
            project_id=int(run.project_id),
            cycle_id=item.cycle_id,
            cycle_item_id=item.id,
            asset_type=item.asset_type or "functional",
            asset_id=item.asset_id if item.asset_id is not None else item.case_id,
            execution_type=EXECUTION_TYPE.get(run_type, "manual"),
            original_run_type=run_type,
            original_run_id=run_id,
            attempt_no=_next_attempt_no(db, item.id),
            status=status,
            summary=summary,
            evidence_json=evidence,
            started_at=now,
            finished_at=now if status in _TERMINAL else None,
            created_by_id=getattr(run, "triggered_by", None),
        )
        db.add(attempt)
        try:
            db.flush()
        except IntegrityError:
            # A concurrent callback created it first: fall back to the update path.
            db.rollback()
            attempt = _find_attempt(db, item.id, run_type, run_id)
            if attempt is None:
                raise
    else:
        # Terminal results are final; a late "running" callback must not undo them.
        if attempt.status in _TERMINAL and status not in _TERMINAL:
            db.commit()
            return attempt
        attempt.status = status
        if summary:
            attempt.summary = summary
        attempt.evidence_json = evidence
        if status in _TERMINAL and attempt.finished_at is None:
            attempt.finished_at = now

    item.attempt_no = max(int(item.attempt_no or 0), int(attempt.attempt_no or 0))
    if status in _TERMINAL:
        _project_onto_item(
            db,
            item,
            status=status,
            run_type=run_type,
            run_id=run_id,
            summary=summary,
            at=now,
        )
    # Do not touch the shared cycle row here: concurrent runs of one batch
    # would serialize on it and can deadlock with item/attempt updates.
    db.commit()
    db.refresh(attempt)
    return attempt
