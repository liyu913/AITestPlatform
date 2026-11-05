"""Workbench: the four things a tester needs to act on right now.

Everything here is derived from real business records (no counters stored on
the side), so a task disappears exactly when its record stops matching. Links
are project-relative and always point at the record that produced the task.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.deps import get_current_user, get_db, require_project_role
from app.models.api_automation.api_automation import ApiRun
from app.models.functional.defect import Defect
from app.models.functional.test_plan import TestCycle, TestCycleItem, TestPlan
from app.models.functional.testcase import TestCase
from app.models.functional.testcase_run import TestCaseRun
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiRun
from app.schemas.project.workbench import (
    WorkbenchBucket,
    WorkbenchOut,
    WorkbenchRecentRun,
    WorkbenchTask,
)
from app.services.functional.testcase_lifecycle import (
    LIFECYCLE_PENDING_REVIEW,
    normalize_lifecycle,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["workbench"])

_PER_BUCKET = 8
_RECENT_TOTAL = 12
_REGRESSION_STATUSES = ("resolved", "fixed", "reopened")
_ACTIVE_CYCLE_STATUSES = ("active", "in_progress")


def _pending_manual_runs(
    db: Session, project_id: int, user_id: int, limit: int
) -> WorkbenchBucket:
    base = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id == project_id,
            TestCycleItem.assignee_id == user_id,
            TestCycleItem.result == "untested",
        )
    )
    total = int(base.count() or 0)
    rows = (
        base.add_columns(TestCycle, TestPlan)
        .order_by(TestCycleItem.updated_at.desc(), TestCycleItem.id.desc())
        .limit(limit)
        .all()
    )
    items: list[WorkbenchTask] = []
    for item, cycle, plan in rows:
        case = db.get(TestCase, item.case_id)
        items.append(
            WorkbenchTask(
                key=f"manual:{item.id}",
                kind="manual_run",
                title=(case.title if case else f"执行项 #{item.id}"),
                subtitle=f"{plan.name} / {cycle.name}",
                status=item.result,
                priority=case.priority if case else None,
                target_type="cycle_item",
                target_id=item.id,
                link=f"cycles/{cycle.id}?item={item.id}",
                updated_at=item.updated_at,
            )
        )
    return WorkbenchBucket(
        key="pending_run",
        label="我的待执行",
        count=total,
        items=items,
        empty_hint="没有指派给你的手工执行项",
    )


def _pending_reviews(db: Session, project_id: int, limit: int) -> WorkbenchBucket:
    base = db.query(TestCase).filter(
        TestCase.project_id == project_id,
        TestCase.lifecycle_status == LIFECYCLE_PENDING_REVIEW,
    )
    total = int(base.count() or 0)
    rows = (
        base.order_by(TestCase.updated_at.desc(), TestCase.id.desc()).limit(limit).all()
    )
    items = [
        WorkbenchTask(
            key=f"review:{case.id}",
            kind="review",
            title=case.title,
            subtitle=f"用例版本 v{max(1, int(case.version_no or 1))}",
            status=normalize_lifecycle(case.lifecycle_status),
            priority=case.priority,
            target_type="test_case",
            target_id=case.id,
            link=f"test-cases?status={LIFECYCLE_PENDING_REVIEW}&case={case.id}",
            updated_at=case.updated_at,
        )
        for case in rows
    ]
    return WorkbenchBucket(
        key="pending_review",
        label="待评审",
        count=total,
        items=items,
        empty_hint="没有待评审的用例版本",
    )


def _pending_regression(
    db: Session, project_id: int, user_id: int, limit: int
) -> WorkbenchBucket:
    base = db.query(Defect).filter(
        Defect.project_id == project_id,
        Defect.status.in_(_REGRESSION_STATUSES),
    )
    mine = base.filter(
        (Defect.assignee_id == user_id) | (Defect.reporter_id == user_id)
    )
    rows = (
        mine.order_by(Defect.updated_at.desc(), Defect.id.desc()).limit(limit).all()
    )
    total = int(mine.count() or 0)
    items = [
        WorkbenchTask(
            key=f"regression:{defect.id}",
            kind="regression",
            title=defect.title,
            subtitle=f"缺陷 #{defect.id} / {defect.status}",
            status=defect.status,
            priority=defect.severity,
            target_type="defect",
            target_id=defect.id,
            link=f"defects?defect={defect.id}",
            updated_at=defect.updated_at,
        )
        for defect in rows
    ]
    return WorkbenchBucket(
        key="pending_regression",
        label="待回归",
        count=total,
        items=items,
        empty_hint="没有等待复测的缺陷",
    )


def _recent_runs(
    db: Session, project_id: int, limit: int
) -> list[WorkbenchRecentRun]:
    rows: list[tuple[str, str, int, str, datetime]] = []
    for kind, label, model, path in (
        ("functional", "功能", TestCaseRun, "test-case-runs"),
        ("api", "接口", ApiRun, "api-runs"),
        ("ui", "UI", UiRun, "ui-runs"),
    ):
        for run in (
            db.query(model)
            .filter(model.project_id == project_id)
            .order_by(model.id.desc())
            .limit(limit)
            .all()
        ):
            rows.append(
                (kind, label, int(run.id), str(run.status), run.created_at or now_beijing())
            )
    rows.sort(key=lambda row: (row[4], row[2]), reverse=True)
    return [
        WorkbenchRecentRun(
            key=f"{kind}:{run_id}",
            kind=kind,  # type: ignore[arg-type]
            kind_label=label,
            id=run_id,
            status=status,
            link=f"{path}/{run_id}",
            created_at=created_at,
        )
        for kind, label, run_id, status, created_at in rows[:limit]
    ]


@router.get("/workbench", response_model=WorkbenchOut)
def project_workbench(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    user: User = Depends(get_current_user),
    per_bucket: int = Query(_PER_BUCKET, ge=1, le=20),
) -> WorkbenchOut:
    return WorkbenchOut(
        project_id=project_id,
        generated_at=now_beijing(),
        buckets=[
            _pending_manual_runs(db, project_id, user.id, per_bucket),
            _pending_reviews(db, project_id, per_bucket),
            _pending_regression(db, project_id, user.id, per_bucket),
        ],
        recent_runs=_recent_runs(db, project_id, _RECENT_TOTAL),
    )
