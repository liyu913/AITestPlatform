"""Case lifecycle, execution-result projection and immutable version snapshots.

Before this module :attr:`TestCase.status` carried two different meanings: the
authoring lifecycle *and* the latest execution result. A failed run therefore
moved an approved case back into a run state, and the status shown in the case
list no longer described the case itself.

The split implemented here is:

* ``lifecycle_status`` — ``draft`` / ``pending_review`` / ``confirmed`` / ``archived``
  (what the author is doing with the case)
* ``last_result``      — ``untested`` / ``passed`` / ``failed`` / ``blocked`` / ``error``
  (projection of the newest execution record)

``TestCase.status`` is kept as a *legacy mirror* of ``lifecycle_status`` so that
older clients, filters and exports keep working; nothing writes an execution
outcome into it any more.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.errors import AppError
from app.models.functional.defect import Defect
from app.models.functional.test_plan import TestCycleItem
from app.models.functional.testcase import TestCase, TestCaseReview, TestCaseVersion
from app.models.functional.testcase_run import TestCaseRun
from app.user_messages import (
    TEST_CASE_ARCHIVED_READONLY,
    TEST_CASE_DELETE_HAS_REFERENCES,
    TEST_CASE_REVIEW_DECISION_INVALID,
    TEST_CASE_STATUS_INVALID,
    TEST_CASE_STATUS_IS_LIFECYCLE,
)

LIFECYCLE_DRAFT = "draft"
LIFECYCLE_PENDING_REVIEW = "pending_review"
LIFECYCLE_CONFIRMED = "confirmed"
LIFECYCLE_ARCHIVED = "archived"

LIFECYCLE_VALUES = (
    LIFECYCLE_DRAFT,
    LIFECYCLE_PENDING_REVIEW,
    LIFECYCLE_CONFIRMED,
    LIFECYCLE_ARCHIVED,
)

LIFECYCLE_LABELS = {
    LIFECYCLE_DRAFT: "草稿",
    LIFECYCLE_PENDING_REVIEW: "待评审",
    LIFECYCLE_CONFIRMED: "已确认",
    LIFECYCLE_ARCHIVED: "已归档",
}

RESULT_UNTESTED = "untested"
RESULT_PASSED = "passed"
RESULT_FAILED = "failed"
RESULT_BLOCKED = "blocked"
RESULT_ERROR = "error"

RESULT_VALUES = (
    RESULT_UNTESTED,
    RESULT_PASSED,
    RESULT_FAILED,
    RESULT_BLOCKED,
    RESULT_ERROR,
)

# Statuses that only ever meant "an execution happened" in the legacy column.
_LEGACY_RESULT_STATUSES = {RESULT_PASSED, RESULT_FAILED, RESULT_BLOCKED, RESULT_ERROR}

_REVIEW_DECISIONS = {"approved": LIFECYCLE_CONFIRMED, "rejected": LIFECYCLE_DRAFT}

# Free-text statuses that already existed in the wild before the split.
_LEGACY_LIFECYCLE_ALIASES = {
    "ready": LIFECYCLE_CONFIRMED,
    "active": LIFECYCLE_CONFIRMED,
    "done": LIFECYCLE_CONFIRMED,
    "new": LIFECYCLE_DRAFT,
    "todo": LIFECYCLE_DRAFT,
}

_SNAPSHOT_TEXT_FIELDS = (
    "title",
    "module",
    "precondition",
    "test_data",
    "case_type",
    "overall_expected",
    "priority",
    "requirement_id",
)


def normalize_lifecycle(raw: object, *, default: str = LIFECYCLE_DRAFT) -> str:
    value = str(raw or "").strip().lower()
    return value if value in LIFECYCLE_VALUES else default


def is_result_status(raw: object) -> bool:
    return str(raw or "").strip().lower() in _LEGACY_RESULT_STATUSES


def derive_from_legacy_status(raw: object) -> tuple[str, str | None]:
    """Split one legacy ``status`` value into ``(lifecycle_status, last_result)``.

    Old rows used ``status`` for both axes, so ``passed``/``failed``/``blocked``
    mean "the case was in use and had that outcome"; a lifecycle word is kept as
    the lifecycle and the result stays unknown.
    """
    value = str(raw or "").strip().lower()
    if value in _LEGACY_RESULT_STATUSES:
        return LIFECYCLE_CONFIRMED, value
    if value in LIFECYCLE_VALUES:
        return value, None
    return LIFECYCLE_DRAFT, None


def apply_lifecycle(
    case: TestCase,
    value: object,
    *,
    actor_id: int | None = None,
    at: object | None = None,
) -> None:
    """Set the lifecycle on ``case`` and keep the legacy mirror in sync."""
    status = normalize_lifecycle(value, default=case.lifecycle_status or LIFECYCLE_DRAFT)
    case.lifecycle_status = status
    case.status = status
    if status == LIFECYCLE_ARCHIVED:
        if case.archived_at is None:
            case.archived_at = at or now_beijing()  # type: ignore[assignment]
        if actor_id is not None:
            case.archived_by_id = actor_id
    else:
        case.archived_at = None
        case.archived_by_id = None


def record_execution_result(
    case: TestCase,
    result: object,
    *,
    at: object | None = None,
) -> bool:
    """Project an execution outcome onto ``case.last_result``.

    Lifecycle is never touched here — that is the whole point of the split.
    Cancelled/unset outcomes return ``False`` and leave the projection alone.
    """
    value = str(result or "").strip().lower()
    if value == "cancelled" or value not in RESULT_VALUES:
        return False
    if value == RESULT_UNTESTED:
        case.last_result = None
        case.last_result_at = None
        return True
    case.last_result = value
    case.last_result_at = at or now_beijing()  # type: ignore[assignment]
    return True


def case_snapshot(case: TestCase) -> dict[str, Any]:
    """Full, read-only description of a case at its current content."""
    snapshot: dict[str, Any] = {}
    for field in _SNAPSHOT_TEXT_FIELDS:
        snapshot[field] = getattr(case, field, None)
    snapshot["tags"] = list(case.tags_json) if isinstance(case.tags_json, list) else None
    snapshot["variables"] = dict(case.variables_json) if isinstance(case.variables_json, dict) else None
    snapshot["design_trace"] = case.design_trace if isinstance(case.design_trace, dict) else None
    snapshot["ai_draft_id"] = case.ai_draft_id
    snapshot["lifecycle_status"] = normalize_lifecycle(case.lifecycle_status)
    snapshot["steps"] = [
        {"order": step.order, "action": step.action, "expected": step.expected}
        for step in sorted(case.steps or [], key=lambda s: s.order)
    ]
    return snapshot


def _add_version(
    db: Session,
    case: TestCase,
    *,
    version_no: int,
    source: str,
    note: str | None,
    actor_id: int | None,
) -> TestCaseVersion:
    version = TestCaseVersion(
        case_id=case.id,
        project_id=case.project_id,
        version_no=version_no,
        lifecycle_status=normalize_lifecycle(case.lifecycle_status),
        snapshot=case_snapshot(case),
        change_source=source[:32],
        change_note=(note or "").strip()[:512] or None,
        created_by_id=actor_id,
    )
    db.add(version)
    db.flush()
    return version


def ensure_baseline_version(
    db: Session,
    case: TestCase,
    *,
    source: str = "migration",
    actor_id: int | None = None,
) -> TestCaseVersion:
    """Store version 1 for an asset that has no history yet (idempotent)."""
    existing = (
        db.query(TestCaseVersion)
        .filter(
            TestCaseVersion.case_id == case.id,
            TestCaseVersion.version_no == (case.version_no or 1),
        )
        .first()
    )
    if existing is not None:
        return existing
    case.version_no = case.version_no or 1
    return _add_version(
        db,
        case,
        version_no=case.version_no,
        source=source,
        note="迁移基准版本",
        actor_id=actor_id,
    )


def snapshot_case_version(
    db: Session,
    case: TestCase,
    *,
    source: str = "edit",
    note: str | None = None,
    actor_id: int | None = None,
) -> TestCaseVersion:
    """Record the *new* content as the next version of the case."""
    case.version_no = max(1, int(case.version_no or 1)) + 1
    return _add_version(
        db,
        case,
        version_no=case.version_no,
        source=source,
        note=note,
        actor_id=actor_id,
    )


def submit_for_review(case: TestCase) -> None:
    if normalize_lifecycle(case.lifecycle_status) == LIFECYCLE_ARCHIVED:
        raise AppError("bad_request", TEST_CASE_ARCHIVED_READONLY, status_code=409)
    if not (case.steps or []):
        raise AppError("bad_request", "用例至少需要一个步骤才能送审", status_code=422)
    apply_lifecycle(case, LIFECYCLE_PENDING_REVIEW)


def review_case(
    db: Session,
    case: TestCase,
    *,
    decision: str,
    comment: str | None,
    reviewer_id: int | None,
    version_id: int | None = None,
) -> TestCaseReview:
    normalized = str(decision or "").strip().lower()
    if normalized not in _REVIEW_DECISIONS:
        raise AppError("bad_request", TEST_CASE_REVIEW_DECISION_INVALID, status_code=400)
    if normalize_lifecycle(case.lifecycle_status) == LIFECYCLE_ARCHIVED:
        raise AppError("bad_request", TEST_CASE_ARCHIVED_READONLY, status_code=409)

    version = None
    if version_id is not None:
        version = (
            db.query(TestCaseVersion)
            .filter(TestCaseVersion.id == version_id, TestCaseVersion.case_id == case.id)
            .first()
        )
    if version is None:
        version = (
            db.query(TestCaseVersion)
            .filter(TestCaseVersion.case_id == case.id)
            .order_by(TestCaseVersion.version_no.desc(), TestCaseVersion.id.desc())
            .first()
        )
    if version is None:
        version = ensure_baseline_version(db, case, source="review", actor_id=reviewer_id)

    apply_lifecycle(case, _REVIEW_DECISIONS[normalized])
    # The review decision is bound to this version; mirror it so the formal
    # publish pointer can rely on the version state as well as the record.
    version.lifecycle_status = (
        LIFECYCLE_CONFIRMED if normalized == "approved" else LIFECYCLE_DRAFT
    )
    review = TestCaseReview(
        case_id=case.id,
        project_id=case.project_id,
        version_id=version.id,
        version_no=version.version_no,
        decision=normalized,
        comment=(comment or "").strip()[:65535],
        reviewer_id=reviewer_id,
    )
    db.add(review)
    db.flush()
    # The confirmed content becomes the version that later runs point at.
    if normalized == "approved" and version.version_no != (case.version_no or 1):
        snapshot_case_version(
            db, case, source="review", note=f"评审通过 v{version.version_no}", actor_id=reviewer_id
        )
    return review


def archive_case(case: TestCase, *, actor_id: int | None = None) -> None:
    apply_lifecycle(case, LIFECYCLE_ARCHIVED, actor_id=actor_id)


def restore_case(case: TestCase, *, actor_id: int | None = None) -> None:
    apply_lifecycle(case, LIFECYCLE_DRAFT, actor_id=actor_id)


def reference_counts(db: Session, case_id: int) -> dict[str, int]:
    return {
        "defects": int(db.query(Defect).filter(Defect.case_id == case_id).count() or 0),
        "cycle_items": int(
            db.query(TestCycleItem).filter(TestCycleItem.case_id == case_id).count() or 0
        ),
        "runs": int(db.query(TestCaseRun).filter(TestCaseRun.case_id == case_id).count() or 0),
    }


def assert_deletable(db: Session, case: TestCase) -> None:
    """Refuse a hard delete once evidence depends on the case; archive instead."""
    counts = reference_counts(db, case.id)
    if any(counts.values()):
        raise AppError(
            "conflict",
            TEST_CASE_DELETE_HAS_REFERENCES.format(
                defects=counts["defects"],
                cycle_items=counts["cycle_items"],
                runs=counts["runs"],
            ),
            status_code=409,
        )


def set_lifecycle_from_payload(
    case: TestCase,
    *,
    lifecycle_status: object = None,
    legacy_status: object = None,
    actor_id: int | None = None,
    import_mode: bool = False,
) -> bool:
    """Apply a lifecycle change from an API payload; reject execution results.

    Clients may still send the legacy ``status`` field. A value that only ever
    meant "an execution happened" is refused so the two axes cannot be merged
    again through the API. ``import_mode`` (create) keeps old payloads working:
    an execution word becomes "confirmed + that result".
    """
    raw = lifecycle_status if lifecycle_status is not None else legacy_status
    if raw is None:
        return False
    value = str(raw).strip().lower()
    if value in _LEGACY_RESULT_STATUSES:
        if not import_mode:
            raise AppError("bad_request", TEST_CASE_STATUS_IS_LIFECYCLE, status_code=400)
        apply_lifecycle(case, LIFECYCLE_CONFIRMED, actor_id=actor_id)
        record_execution_result(case, value)
        return True
    value = _LEGACY_LIFECYCLE_ALIASES.get(value, value)
    if value not in LIFECYCLE_VALUES:
        if import_mode:
            value = LIFECYCLE_DRAFT
        else:
            raise AppError(
                "bad_request",
                TEST_CASE_STATUS_INVALID.format(allowed="、".join(LIFECYCLE_VALUES)),
                status_code=400,
            )
    apply_lifecycle(case, value, actor_id=actor_id)
    return True
