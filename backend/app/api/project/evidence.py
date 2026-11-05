"""Project-wide evidence search: plan / case / batch -> attempts."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.models.functional.test_plan import TestCycle, TestCycleAttempt, TestCycleItem, TestPlan
from app.models.api_automation.api_automation import ApiCase
from app.models.functional.testcase import TestCase
from app.models.ui_automation.ui_automation import UiCase
from app.models.project.project import ProjectMember
from app.schemas.page import Page
from app.schemas.project.evidence import EvidenceOut

router = APIRouter(prefix="/projects/{project_id}/evidence", tags=["evidence"])


@router.get("/", response_model=Page[EvidenceOut])
def search_evidence(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    plan_id: int | None = Query(None, ge=1),
    cycle_id: int | None = Query(None, ge=1),
    case_id: int | None = Query(None, ge=1),
    status: str | None = Query(None, max_length=32),
) -> Page[EvidenceOut]:
    query = (
        db.query(TestCycleAttempt, TestCycle, TestPlan, TestCycleItem, TestCase)
        .join(TestCycleItem, TestCycleAttempt.cycle_item_id == TestCycleItem.id)
        .join(TestCycle, TestCycleAttempt.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .outerjoin(TestCase, TestCycleItem.case_id == TestCase.id)
        .filter(TestPlan.project_id == project_id)
    )
    if plan_id is not None:
        query = query.filter(TestCycle.plan_id == plan_id)
    if cycle_id is not None:
        query = query.filter(TestCycleAttempt.cycle_id == cycle_id)
    if case_id is not None:
        query = query.filter(TestCycleItem.case_id == case_id)
    if status:
        query = query.filter(TestCycleAttempt.status == status)

    total = query.count()
    rows = (
        query.order_by(TestCycleAttempt.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    def asset_title(asset_type: str, asset_id: int | None) -> str | None:
        if asset_id is None:
            return None
        model = {"api_case": ApiCase, "ui_case": UiCase}.get(asset_type)
        if model is None:
            return None
        row = db.get(model, asset_id)
        return getattr(row, "name", None) if row is not None else None

    items = [
        EvidenceOut(
            attempt_id=attempt.id,
            plan_id=plan.id,
            plan_name=plan.name,
            cycle_id=cycle.id,
            cycle_name=cycle.name,
            item_id=item.id,
            asset_type=item.asset_type or attempt.asset_type,
            case_id=case.id if case is not None else None,
            case_title=(
                case.title
                if case is not None
                else asset_title(item.asset_type or attempt.asset_type, item.asset_id)
            ),
            attempt_no=attempt.attempt_no,
            execution_type=attempt.execution_type,
            status=attempt.status,
            summary=attempt.summary or "",
            original_run_type=attempt.original_run_type,
            original_run_id=attempt.original_run_id,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            evidence=attempt.evidence_json if isinstance(attempt.evidence_json, dict) else None,
        )
        for attempt, cycle, plan, item, case in rows
    ]
    return Page[EvidenceOut](items=items, total=total, page=page, page_size=page_size)
