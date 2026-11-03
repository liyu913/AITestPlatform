from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.datetime_json import ensure_beijing, now_beijing
from app.deps import get_db, require_project_role
from app.models.api_automation.api_automation import ApiCase, ApiRun, ApiSuite
from app.models.functional.defect import Defect
from app.models.functional.requirement import Requirement
from app.models.functional.test_plan import TestPlan
from app.models.functional.testcase import TestCase
from app.models.functional.testcase_run import TestCaseRun
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.project.project import ProjectMember, ProjectModule
from app.models.ui_automation.ui_automation import UiCase, UiRun, UiSuite
from app.schemas.project.dashboard import (
    CountBucket,
    CoverageSummary,
    DashboardAssets,
    DashboardRuns,
    ProjectDashboardOut,
    RecentRun,
    RunTrend,
    RunTrendPoint,
)
from app.services.functional.coverage_stats import coverage_summary

router = APIRouter(prefix="/projects/{project_id}", tags=["dashboard"])

_RECENT_PER_KIND = 8
_RECENT_TOTAL = 12
_EMPTY_TREND_TOTALS = {"total": 0, "passed": 0, "failed": 0, "error": 0}
RunKind = Literal["functional", "api", "ui"]


def _count_bucket(
    db: Session, model: type, project_id: int, status_attr: str | None = "status"
) -> CountBucket:
    if status_attr is None:
        total = db.query(func.count(model.id)).filter(model.project_id == project_id).scalar()
        return CountBucket(total=int(total or 0), by_status={})
    col = getattr(model, status_attr)
    rows = (
        db.query(col, func.count(model.id))
        .filter(model.project_id == project_id)
        .group_by(col)
        .all()
    )
    by_status: dict[str, int] = {}
    total = 0
    for key, count in rows:
        n = int(count or 0)
        total += n
        by_status[str(key) if key is not None else ""] = n
    return CountBucket(total=total, by_status=by_status)


def _trend_from_rows(rows: list[tuple[datetime | None, str]]) -> RunTrend:
    by_day: dict[str, dict[str, int]] = {}
    totals = dict(_EMPTY_TREND_TOTALS)
    for created_at, status in rows:
        day = ensure_beijing(created_at or now_beijing()).strftime("%Y-%m-%d")
        bucket = by_day.setdefault(day, dict(_EMPTY_TREND_TOTALS))
        bucket["total"] += 1
        totals["total"] += 1
        if status in ("passed", "failed", "error"):
            bucket[status] += 1
            totals[status] += 1
    points = [
        RunTrendPoint(
            day=day,
            total=values["total"],
            passed=values["passed"],
            failed=values["failed"],
            error=values["error"],
        )
        for day, values in sorted(by_day.items())
    ]
    return RunTrend(points=points, totals=totals)


def _run_trend(db: Session, model: type, project_id: int, since: datetime) -> RunTrend:
    rows = (
        db.query(model.created_at, model.status)
        .filter(model.project_id == project_id, model.created_at >= since)
        .all()
    )
    return _trend_from_rows(list(rows))


def _recent_kind(
    db: Session, model: type, project_id: int, kind: RunKind
) -> list[tuple[RunKind, int, str, datetime]]:
    rows = (
        db.query(model.id, model.status, model.created_at)
        .filter(model.project_id == project_id)
        .order_by(model.id.desc())
        .limit(_RECENT_PER_KIND)
        .all()
    )
    out: list[tuple[RunKind, int, str, datetime]] = []
    for run_id, status, created_at in rows:
        out.append((kind, int(run_id), str(status), created_at or now_beijing()))
    return out


@router.get("/dashboard", response_model=ProjectDashboardOut)
def project_dashboard(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    days: int = Query(14, ge=1, le=90),
) -> ProjectDashboardOut:
    since = now_beijing() - timedelta(days=days)
    req_total, blind_count, avg_ratio = coverage_summary(db, project_id)

    recent: list[tuple[RunKind, int, str, datetime]] = []
    recent.extend(_recent_kind(db, TestCaseRun, project_id, "functional"))
    recent.extend(_recent_kind(db, ApiRun, project_id, "api"))
    recent.extend(_recent_kind(db, UiRun, project_id, "ui"))
    recent.sort(key=lambda item: (item[3], item[1]), reverse=True)

    return ProjectDashboardOut(
        assets=DashboardAssets(
            requirements=_count_bucket(db, Requirement, project_id),
            test_cases=_count_bucket(db, TestCase, project_id),
            defects=_count_bucket(db, Defect, project_id),
            test_plans=_count_bucket(db, TestPlan, project_id),
            knowledge_documents=_count_bucket(db, KnowledgeDocument, project_id, "parse_status"),
            modules=_count_bucket(db, ProjectModule, project_id, None),
            members=_count_bucket(db, ProjectMember, project_id, "role"),
            api_cases=_count_bucket(db, ApiCase, project_id),
            api_suites=_count_bucket(db, ApiSuite, project_id, None),
            ui_cases=_count_bucket(db, UiCase, project_id),
            ui_suites=_count_bucket(db, UiSuite, project_id, None),
        ),
        coverage=CoverageSummary(
            requirement_total=req_total,
            blind_count=blind_count,
            avg_ratio=avg_ratio,
        ),
        runs=DashboardRuns(
            functional=_run_trend(db, TestCaseRun, project_id, since),
            api=_run_trend(db, ApiRun, project_id, since),
            ui=_run_trend(db, UiRun, project_id, since),
        ),
        recent_runs=[
            RecentRun(kind=kind, id=run_id, status=status, created_at=created_at)
            for kind, run_id, status, created_at in recent[:_RECENT_TOTAL]
        ],
    )
