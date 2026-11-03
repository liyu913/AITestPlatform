from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.functional.requirement import Requirement
from app.models.functional.testcase import TestCase
from app.models.functional.test_plan import TestCycle, TestCycleItem, TestPlan
from app.schemas.functional.test_plan import RequirementCoverageOut
from app.user_messages import TEST_CYCLE_NOT_FOUND


def latest_results_by_case(
    db: Session, project_id: int, cycle_id: int | None
) -> dict[int, str]:
    """Map case_id -> result. Default cycle: latest executed non-untested; else untested."""
    if cycle_id is not None:
        cycle = (
            db.query(TestCycle)
            .join(TestPlan, TestCycle.plan_id == TestPlan.id)
            .filter(TestCycle.id == cycle_id, TestPlan.project_id == project_id)
            .first()
        )
        if cycle is None:
            raise AppError("not_found", TEST_CYCLE_NOT_FOUND, status_code=404)
        items = db.query(TestCycleItem).filter(TestCycleItem.cycle_id == cycle_id).all()
        return {it.case_id: it.result for it in items}

    items = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id == project_id,
            TestCycleItem.result.in_(("passed", "failed", "blocked")),
            TestCycleItem.executed_at.isnot(None),
        )
        .order_by(TestCycleItem.executed_at.desc(), TestCycleItem.id.desc())
        .all()
    )
    latest: dict[int, str] = {}
    for it in items:
        if it.case_id not in latest:
            latest[it.case_id] = it.result
    return latest


def requirement_coverage_rows(
    db: Session, project_id: int, cycle_id: int | None = None
) -> list[RequirementCoverageOut]:
    latest = latest_results_by_case(db, project_id, cycle_id)
    requirements = (
        db.query(Requirement)
        .filter(Requirement.project_id == project_id)
        .order_by(Requirement.id)
        .all()
    )
    cases = db.query(TestCase).filter(TestCase.project_id == project_id).all()
    cases_by_req: dict[int | None, list[TestCase]] = {}
    for c in cases:
        cases_by_req.setdefault(c.requirement_id, []).append(c)

    out: list[RequirementCoverageOut] = []
    for req in requirements:
        linked = cases_by_req.get(req.id, [])
        passed = failed = blocked = untested = 0
        for c in linked:
            result = latest.get(c.id, "untested")
            if result == "passed":
                passed += 1
            elif result == "failed":
                failed += 1
            elif result == "blocked":
                blocked += 1
            else:
                untested += 1
        total = len(linked)
        tested = passed + failed + blocked
        ratio = (tested / total) if total > 0 else 0.0
        blind = total == 0 or untested > 0 or failed > 0
        out.append(
            RequirementCoverageOut(
                requirement_id=req.id,
                title=req.title,
                status=req.status,
                priority=req.priority,
                case_total=total,
                passed=passed,
                failed=failed,
                blocked=blocked,
                untested=untested,
                coverage_ratio=round(ratio, 4),
                blind=blind,
            )
        )
    return out


def coverage_summary(db: Session, project_id: int) -> tuple[int, int, float]:
    rows = requirement_coverage_rows(db, project_id, None)
    if not rows:
        return 0, 0, 0.0
    blind_count = sum(1 for row in rows if row.blind)
    avg_ratio = round(sum(row.coverage_ratio for row in rows) / len(rows), 4)
    return len(rows), blind_count, avg_ratio
