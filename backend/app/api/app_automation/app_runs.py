from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.deps import get_db, require_project_role
from app.errors import AppError
from app.models.app_automation.app_automation import AppRun, AppRunStep
from app.models.project.project import ProjectMember
from app.schemas.app_automation.app_automation import AppRunOut, AppRunStepOut, AppTrendOut, AppTrendPoint
from app.schemas.page import Page
from app.services.app_automation.app_report import build_html_report, build_junit_xml
from app.services.file_storage import absolute_path_for_storage_key
from app.user_messages import APP_RUN_NOT_FOUND, APP_SCREENSHOT_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/app-runs", tags=["app-runs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_run(db: Session, project_id: int, run_id: int) -> AppRun:
    row = db.query(AppRun).filter(AppRun.id == run_id, AppRun.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", APP_RUN_NOT_FOUND, status_code=404)
    return row


@router.get("/", response_model=Page[AppRunOut])
def list_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, max_length=32),
    trigger_type: str | None = Query(None, max_length=32),
) -> Page[AppRunOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(AppRun).filter(AppRun.project_id == project_id)
    if status:
        filtered = filtered.filter(AppRun.status == status)
    if trigger_type:
        filtered = filtered.filter(AppRun.trigger_type == trigger_type)
    total = filtered.count()
    rows = filtered.order_by(AppRun.updated_at.desc(), AppRun.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[AppRunOut](
        items=[AppRunOut.model_validate(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/trends", response_model=AppTrendOut)
def run_trends(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    days: int = Query(14, ge=1, le=90),
) -> AppTrendOut:
    since = now_beijing() - timedelta(days=days)
    rows = (
        db.query(AppRun)
        .filter(AppRun.project_id == project_id, AppRun.created_at >= since)
        .order_by(AppRun.id.asc())
        .all()
    )
    by_day: dict[str, dict[str, int]] = {}
    totals = {"total": 0, "passed": 0, "failed": 0, "error": 0}
    for r in rows:
        day = (r.created_at or now_beijing()).strftime("%Y-%m-%d")
        bucket = by_day.setdefault(day, {"total": 0, "passed": 0, "failed": 0, "error": 0})
        bucket["total"] += 1
        totals["total"] += 1
        st = r.status
        if st in ("passed", "failed", "error"):
            bucket[st] += 1
            totals[st] += 1
    points = [
        AppTrendPoint(
            day=d,
            total=v["total"],
            passed=v["passed"],
            failed=v["failed"],
            error=v["error"],
        )
        for d, v in sorted(by_day.items())
    ]
    return AppTrendOut(points=points, totals=totals)


@router.get("/{run_id}", response_model=AppRunOut)
def get_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> AppRunOut:
    return AppRunOut.model_validate(_require_run(db, project_id, run_id))


@router.get("/{run_id}/steps", response_model=list[AppRunStepOut])
def list_run_steps(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[AppRunStepOut]:
    _require_run(db, project_id, run_id)
    rows = (
        db.query(AppRunStep)
        .filter(AppRunStep.run_id == run_id, AppRunStep.project_id == project_id)
        .order_by(AppRunStep.order, AppRunStep.id)
        .all()
    )
    return [AppRunStepOut.model_validate(r) for r in rows]


@router.get("/{run_id}/export")
def export_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    format: str = Query("junit", pattern="^(junit|html)$"),
) -> Response:
    run = _require_run(db, project_id, run_id)
    rows = (
        db.query(AppRunStep)
        .filter(AppRunStep.run_id == run_id, AppRunStep.project_id == project_id)
        .order_by(AppRunStep.order, AppRunStep.id)
        .all()
    )
    steps = [
        {
            "order": r.order,
            "case_id": r.case_id,
            "case_name": r.case_name,
            "action": r.action,
            "selector": r.selector,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "error_message": r.error_message or "",
        }
        for r in rows
    ]
    if format == "html":
        content = build_html_report(
            run_id=run.id,
            project_id=project_id,
            status=run.status,
            summary=run.summary_json if isinstance(run.summary_json, dict) else None,
            steps=steps,
        )
        return Response(
            content=content,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="app-run-{run_id}.html"'},
        )
    content = build_junit_xml(run_id=run.id, project_id=project_id, status=run.status, steps=steps)
    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="app-run-{run_id}.xml"'},
    )


@router.get("/{run_id}/steps/{step_id}/screenshot")
def get_step_screenshot(
    project_id: int,
    run_id: int,
    step_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> FileResponse:
    _require_run(db, project_id, run_id)
    step = (
        db.query(AppRunStep)
        .filter(
            AppRunStep.id == step_id,
            AppRunStep.run_id == run_id,
            AppRunStep.project_id == project_id,
        )
        .first()
    )
    if step is None or not step.screenshot_key:
        raise AppError("not_found", APP_SCREENSHOT_NOT_FOUND, status_code=404)
    path: Path = absolute_path_for_storage_key(step.screenshot_key)
    if not path.is_file():
        raise AppError("not_found", APP_SCREENSHOT_NOT_FOUND, status_code=404)
    if not step.screenshot_key.startswith(f"{project_id}/"):
        raise AppError("not_found", APP_SCREENSHOT_NOT_FOUND, status_code=404)
    return FileResponse(path, media_type="image/png", filename=path.name)
