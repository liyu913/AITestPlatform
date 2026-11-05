"""Batch reports: generate immutable snapshots, browse history, export (B6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.test_plan import TestCycle, TestCycleReport, TestPlan
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.schemas.functional.report import (
    ReportCompareOut,
    ReportMetricDelta,
    ReportOut,
    ReportRequirementChange,
    ReportSummaryOut,
)
from app.services.functional.cycle_report import (
    generate_report,
    report_to_markdown,
    report_to_xlsx,
)
from app.user_messages import TEST_CYCLE_NOT_FOUND, TEST_REPORT_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])


def _require_cycle(db: Session, project_id: int, cycle_id: int) -> TestCycle:
    row = (
        db.query(TestCycle)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycle.id == cycle_id, TestPlan.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", TEST_CYCLE_NOT_FOUND, status_code=404)
    return row


def _require_report(db: Session, project_id: int, report_id: int) -> TestCycleReport:
    row = (
        db.query(TestCycleReport)
        .filter(TestCycleReport.id == report_id, TestCycleReport.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", TEST_REPORT_NOT_FOUND, status_code=404)
    return row


_METRIC_LABELS = {
    "selected_total": "选定计划项",
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻塞",
    "untested": "未执行",
    "running": "运行中",
    "skipped": "跳过/取消",
    "pass_rate": "通过率",
    "completion_rate": "完成比例",
    "retried_to_pass": "重试后通过",
    "flaky": "不稳定",
}

_RATE_KEYS = {"pass_rate", "completion_rate"}


def _compare_snapshots(
    *, current: TestCycleReport, previous: TestCycleReport
) -> ReportCompareOut:
    current_snapshot = current.snapshot_json if isinstance(current.snapshot_json, dict) else {}
    previous_snapshot = previous.snapshot_json if isinstance(previous.snapshot_json, dict) else {}
    current_metrics = current_snapshot.get("metrics") or {}
    previous_metrics = previous_snapshot.get("metrics") or {}

    metrics: list[ReportMetricDelta] = []
    for key, label in _METRIC_LABELS.items():
        cur = current_metrics.get(key)
        prev = previous_metrics.get(key)
        delta = None
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
            delta = round(float(cur) - float(prev), 4)
        metrics.append(
            ReportMetricDelta(key=key, label=label, current=cur, previous=prev, delta=delta)
        )

    current_risks = {str(r.get("message")) for r in current_snapshot.get("risks") or [] if r.get("message")}
    previous_risks = {str(r.get("message")) for r in previous_snapshot.get("risks") or [] if r.get("message")}

    current_requirements = {
        int(r["requirement_id"]): r for r in current_snapshot.get("requirements") or [] if "requirement_id" in r
    }
    previous_requirements = {
        int(r["requirement_id"]): r for r in previous_snapshot.get("requirements") or [] if "requirement_id" in r
    }
    changed: list[ReportRequirementChange] = []
    for requirement_id in sorted(set(current_requirements) | set(previous_requirements)):
        cur = current_requirements.get(requirement_id) or {}
        prev = previous_requirements.get(requirement_id) or {}
        if (
            cur.get("coverage_ratio") != prev.get("coverage_ratio")
            or cur.get("case_total") != prev.get("case_total")
            or cur.get("passed") != prev.get("passed")
            or cur.get("failed") != prev.get("failed")
        ):
            changed.append(
                ReportRequirementChange(
                    requirement_id=requirement_id,
                    title=str(cur.get("title") or prev.get("title") or f"需求 #{requirement_id}"),
                    previous_ratio=prev.get("coverage_ratio"),
                    current_ratio=cur.get("coverage_ratio"),
                    previous_case_total=int(prev.get("case_total") or 0),
                    current_case_total=int(cur.get("case_total") or 0),
                )
            )

    return ReportCompareOut(
        cycle_id=current.cycle_id,
        current_id=current.id,
        previous_id=previous.id,
        current_revision=current.revision,
        previous_revision=previous.revision,
        metrics=metrics,
        risks_added=sorted(current_risks - previous_risks),
        risks_resolved=sorted(previous_risks - current_risks),
        requirements_changed=changed,
    )


def _summary(row: TestCycleReport, cycle: TestCycle | None, plan: TestPlan | None) -> ReportSummaryOut:
    snapshot = row.snapshot_json if isinstance(row.snapshot_json, dict) else {}
    metrics = snapshot.get("metrics") or {}
    return ReportSummaryOut(
        id=row.id,
        project_id=row.project_id,
        cycle_id=row.cycle_id,
        revision=row.revision,
        generated_at=row.generated_at,
        generated_by_id=row.generated_by_id,
        summary_text=row.summary_text or "",
        cycle_name=cycle.name if cycle else (snapshot.get("cycle") or {}).get("name"),
        plan_name=plan.name if plan else (snapshot.get("cycle") or {}).get("plan_name"),
        target_build=(snapshot.get("cycle") or {}).get("target_build"),
        pass_rate=metrics.get("pass_rate"),
        completion_rate=metrics.get("completion_rate"),
        risk_count=len(snapshot.get("risks") or []),
    )


def _detail(row: TestCycleReport, cycle: TestCycle | None, plan: TestPlan | None) -> ReportOut:
    base = _summary(row, cycle, plan)
    return ReportOut(
        **base.model_dump(),
        snapshot=row.snapshot_json if isinstance(row.snapshot_json, dict) else None,
    )


@router.post("/cycles/{cycle_id}", response_model=ReportOut, status_code=201)
def create_report(
    project_id: int,
    cycle_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ReportOut:
    cycle = _require_cycle(db, project_id, cycle_id)
    report = generate_report(db, project_id, cycle, user.id)
    db.commit()
    db.refresh(report)
    return _detail(report, cycle, db.get(TestPlan, cycle.plan_id))


@router.get("/cycles/{cycle_id}", response_model=list[ReportSummaryOut])
def list_cycle_reports(
    project_id: int,
    cycle_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[ReportSummaryOut]:
    cycle = _require_cycle(db, project_id, cycle_id)
    plan = db.get(TestPlan, cycle.plan_id)
    rows = (
        db.query(TestCycleReport)
        .filter(TestCycleReport.cycle_id == cycle_id, TestCycleReport.project_id == project_id)
        .order_by(TestCycleReport.revision.desc())
        .all()
    )
    return [_summary(row, cycle, plan) for row in rows]


@router.get("", response_model=list[ReportSummaryOut])
def list_project_reports(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    limit: int = Query(50, ge=1, le=200),
) -> list[ReportSummaryOut]:
    rows = (
        db.query(TestCycleReport)
        .filter(TestCycleReport.project_id == project_id)
        .order_by(TestCycleReport.id.desc())
        .limit(limit)
        .all()
    )
    cycles: dict[int, TestCycle] = {}
    plans: dict[int, TestPlan] = {}
    out: list[ReportSummaryOut] = []
    for row in rows:
        cycle = cycles.get(row.cycle_id)
        if cycle is None:
            cycle = db.get(TestCycle, row.cycle_id)
            if cycle is not None:
                cycles[row.cycle_id] = cycle
        plan = None
        if cycle is not None:
            plan = plans.get(cycle.plan_id)
            if plan is None:
                plan = db.get(TestPlan, cycle.plan_id)
                if plan is not None:
                    plans[cycle.plan_id] = plan
        out.append(_summary(row, cycle, plan))
    return out


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    project_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ReportOut:
    row = _require_report(db, project_id, report_id)
    cycle = db.get(TestCycle, row.cycle_id)
    plan = db.get(TestPlan, cycle.plan_id) if cycle is not None else None
    return _detail(row, cycle, plan)


@router.get("/{report_id}/compare", response_model=ReportCompareOut)
def compare_report(
    project_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    against: int | None = Query(None, ge=1),
) -> ReportCompareOut:
    """Compare a report with an earlier revision of the same batch."""
    current = _require_report(db, project_id, report_id)
    if against is None:
        previous = (
            db.query(TestCycleReport)
            .filter(
                TestCycleReport.cycle_id == current.cycle_id,
                TestCycleReport.revision < current.revision,
            )
            .order_by(TestCycleReport.revision.desc())
            .first()
        )
        if previous is None:
            raise AppError("bad_request", "没有可对比的历史版本", status_code=400)
    else:
        previous = _require_report(db, project_id, against)
        if previous.cycle_id != current.cycle_id:
            raise AppError("bad_request", "只能对比同一批次的报告", status_code=400)
        if previous.id == current.id:
            raise AppError("bad_request", "不能与自身对比", status_code=400)
    return _compare_snapshots(current=current, previous=previous)


@router.get("/{report_id}/export")
def export_report(
    project_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    format: str = Query("md", pattern="^(md|xlsx)$"),
) -> Response:
    row = _require_report(db, project_id, report_id)
    snapshot = row.snapshot_json if isinstance(row.snapshot_json, dict) else {}
    if format == "xlsx":
        content = report_to_xlsx(snapshot)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="report-{row.cycle_id}-r{row.revision}.xlsx"'
            },
        )
    content = report_to_markdown(snapshot).encode("utf-8")
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="report-{row.cycle_id}-r{row.revision}.md"'
        },
    )
