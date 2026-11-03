from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing, to_beijing_iso
from app.deps import get_db, require_project_role
from app.errors import AppError
from app.models.perf.perf_automation import PerfRun
from app.models.project.project import ProjectMember
from app.schemas.page import Page
from app.schemas.perf.perf_automation import (
    PerfCompareMetric,
    PerfCompareOut,
    PerfRunOut,
    PerfTrendOut,
    PerfTrendPoint,
)
from app.services.perf.perf_report import build_html_report, compare_metrics, extract_metrics
from app.user_messages import PERF_RUN_NOT_CANCELLABLE, PERF_RUN_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/perf-runs", tags=["perf-runs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_run(db: Session, project_id: int, run_id: int) -> PerfRun:
    row = db.query(PerfRun).filter(PerfRun.id == run_id, PerfRun.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", PERF_RUN_NOT_FOUND, status_code=404)
    return row


def _utc_now_iso() -> str:
    return to_beijing_iso(now_beijing())


def _created_iso(row: PerfRun) -> str | None:
    if row.created_at is None:
        return None
    try:
        return row.created_at.isoformat()
    except Exception:
        return str(row.created_at)


def _trend_point(row: PerfRun) -> PerfTrendPoint:
    snap = row.config_snapshot_json if isinstance(row.config_snapshot_json, dict) else {}
    metrics = extract_metrics(row.summary_json if isinstance(row.summary_json, dict) else None)
    return PerfTrendPoint(
        run_id=row.id,
        scenario_id=row.scenario_id,
        scenario_name=str(snap.get("scenario_name")) if snap.get("scenario_name") else None,
        status=row.status,
        created_at=_created_iso(row),
        finished_at=row.finished_at,
        p95_ms=metrics.get("p95_ms"),  # type: ignore[arg-type]
        p90_ms=metrics.get("p90_ms"),  # type: ignore[arg-type]
        avg_ms=metrics.get("avg_ms"),  # type: ignore[arg-type]
        rps=metrics.get("rps"),  # type: ignore[arg-type]
        fail_ratio=metrics.get("fail_ratio"),  # type: ignore[arg-type]
        total_requests=metrics.get("total_requests"),  # type: ignore[arg-type]
    )


def _find_baseline(
    db: Session,
    *,
    project_id: int,
    current: PerfRun,
    baseline_run_id: int | None,
) -> PerfRun | None:
    if baseline_run_id is not None:
        row = (
            db.query(PerfRun)
            .filter(PerfRun.id == baseline_run_id, PerfRun.project_id == project_id)
            .first()
        )
        if row is None:
            raise AppError("not_found", PERF_RUN_NOT_FOUND, detail=str(baseline_run_id), status_code=404)
        return row
    if current.scenario_id is None:
        return None
    return (
        db.query(PerfRun)
        .filter(
            PerfRun.project_id == project_id,
            PerfRun.scenario_id == current.scenario_id,
            PerfRun.id < current.id,
            PerfRun.status.in_(("passed", "failed")),
        )
        .order_by(PerfRun.id.desc())
        .first()
    )


@router.get("/", response_model=Page[PerfRunOut])
def list_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scenario_id: int | None = Query(None),
    status: str | None = Query(None, max_length=32),
) -> Page[PerfRunOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(PerfRun).filter(PerfRun.project_id == project_id)
    if scenario_id is not None:
        filtered = filtered.filter(PerfRun.scenario_id == scenario_id)
    if status and status.strip():
        filtered = filtered.filter(PerfRun.status == status.strip())
    total = filtered.count()
    rows = filtered.order_by(PerfRun.updated_at.desc(), PerfRun.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[PerfRunOut](
        items=[PerfRunOut.model_validate(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/trends", response_model=PerfTrendOut)
def run_trends(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    days: int = Query(14, ge=1, le=90),
    scenario_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> PerfTrendOut:
    since = now_beijing() - timedelta(days=days)
    q = db.query(PerfRun).filter(PerfRun.project_id == project_id, PerfRun.created_at >= since)
    if scenario_id is not None:
        q = q.filter(PerfRun.scenario_id == scenario_id)
    rows = q.order_by(PerfRun.id.asc()).limit(limit).all()
    totals = {"total": 0, "passed": 0, "failed": 0, "error": 0, "cancelled": 0}
    points: list[PerfTrendPoint] = []
    for r in rows:
        totals["total"] += 1
        if r.status in totals:
            totals[r.status] += 1
        if r.status in ("passed", "failed", "cancelled", "error") and isinstance(r.summary_json, dict):
            points.append(_trend_point(r))
        elif r.status in ("passed", "failed"):
            points.append(_trend_point(r))
    return PerfTrendOut(points=points, totals=totals)


@router.get("/{run_id}", response_model=PerfRunOut)
def get_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> PerfRunOut:
    return PerfRunOut.model_validate(_require_run(db, project_id, run_id))


@router.get("/{run_id}/compare", response_model=PerfCompareOut)
def compare_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    baseline_run_id: int | None = Query(None),
) -> PerfCompareOut:
    current = _require_run(db, project_id, run_id)
    baseline = _find_baseline(db, project_id=project_id, current=current, baseline_run_id=baseline_run_id)
    cur_summary = current.summary_json if isinstance(current.summary_json, dict) else None
    base_summary = baseline.summary_json if baseline and isinstance(baseline.summary_json, dict) else None
    rows = compare_metrics(cur_summary, base_summary) if baseline else []
    return PerfCompareOut(
        current_run_id=current.id,
        baseline_run_id=baseline.id if baseline else None,
        baseline_finished_at=baseline.finished_at if baseline else None,
        metrics=[PerfCompareMetric.model_validate(r) for r in rows],
        current_summary=cur_summary,
        baseline_summary=base_summary,
    )


@router.get("/{run_id}/export")
def export_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    format: str = Query("html", pattern="^(html)$"),
    baseline_run_id: int | None = Query(None),
) -> Response:
    run = _require_run(db, project_id, run_id)
    summary = run.summary_json if isinstance(run.summary_json, dict) else None
    config = run.config_snapshot_json if isinstance(run.config_snapshot_json, dict) else None
    compare_rows = None
    baseline = _find_baseline(db, project_id=project_id, current=run, baseline_run_id=baseline_run_id)
    if baseline is not None:
        base_summary = baseline.summary_json if isinstance(baseline.summary_json, dict) else None
        compare_rows = compare_metrics(summary, base_summary)
    content = build_html_report(
        run_id=run.id,
        project_id=project_id,
        status=run.status,
        summary=summary,
        config=config,
        compare_rows=compare_rows,
    )
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="perf-run-{run_id}.html"'},
    )


@router.post("/{run_id}/cancel", response_model=PerfRunOut)
def cancel_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> PerfRunOut:
    run = _require_run(db, project_id, run_id)
    if run.status not in ("pending", "running"):
        raise AppError("bad_request", PERF_RUN_NOT_CANCELLABLE, status_code=400)

    was_pending = run.status == "pending"
    run.status = "cancelled"
    if was_pending:
        run.finished_at = _utc_now_iso()
        summary = run.summary_json if isinstance(run.summary_json, dict) else {}
        run.summary_json = {**summary, "cancelled": True, "error": "用户取消"}
    db.commit()
    db.refresh(run)

    if run.celery_task_id:
        try:
            from app.tasks.celery_app import celery_app

            celery_app.control.revoke(run.celery_task_id, terminate=False)
        except Exception:
            pass

    return PerfRunOut.model_validate(run)
