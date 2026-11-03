from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiEnvironment, UiRun, UiRunStep, UiSuite
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.ui_automation.ui_automation import (
    UiCiTriggerRequest,
    UiRunFixConfirm,
    UiRunFixOut,
    UiRunFixPlan,
    UiRunOut,
    UiRunStepOut,
    UiTrendOut,
    UiTrendPoint,
)
from app.services.ai.effective_ai_settings import get_llm_runtime
from app.services.file_storage import absolute_path_for_storage_key
from app.services.ui_automation.ui_report import build_html_report, build_junit_xml
from app.services.ui_automation.ui_run_fix import confirm_fix, get_fix, hydrate_fix, start_repair_explore
from app.tasks.ui_automation.run_ui_case import enqueue_run_ui_case
from app.user_messages import UI_ENV_NOT_FOUND, UI_RUN_NOT_FOUND, UI_SCREENSHOT_NOT_FOUND, UI_SUITE_EMPTY, UI_SUITE_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/ui-runs", tags=["ui-runs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_run(db: Session, project_id: int, run_id: int) -> UiRun:
    row = db.query(UiRun).filter(UiRun.id == run_id, UiRun.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", UI_RUN_NOT_FOUND, status_code=404)
    return row


@router.get("/", response_model=Page[UiRunOut])
def list_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, max_length=32),
    trigger_type: str | None = Query(None, max_length=32),
) -> Page[UiRunOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(UiRun).filter(UiRun.project_id == project_id)
    if status:
        filtered = filtered.filter(UiRun.status == status)
    if trigger_type:
        filtered = filtered.filter(UiRun.trigger_type == trigger_type)
    total = filtered.count()
    rows = filtered.order_by(UiRun.updated_at.desc(), UiRun.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[UiRunOut](
        items=[UiRunOut.model_validate(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/trends", response_model=UiTrendOut)
def run_trends(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    days: int = Query(14, ge=1, le=90),
) -> UiTrendOut:
    since = now_beijing() - timedelta(days=days)
    rows = (
        db.query(UiRun)
        .filter(UiRun.project_id == project_id, UiRun.created_at >= since)
        .order_by(UiRun.id.asc())
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
        UiTrendPoint(
            day=d,
            total=v["total"],
            passed=v["passed"],
            failed=v["failed"],
            error=v["error"],
        )
        for d, v in sorted(by_day.items())
    ]
    return UiTrendOut(points=points, totals=totals)


@router.post("/ci-trigger", response_model=list[UiRunOut], status_code=201)
def ci_trigger(
    project_id: int,
    body: UiCiTriggerRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[UiRunOut]:
    """CI/Webhook entry: create suite runs (auth via JWT / project role)."""
    suite = (
        db.query(UiSuite)
        .filter(UiSuite.id == body.suite_id, UiSuite.project_id == project_id)
        .first()
    )
    if suite is None:
        raise AppError("not_found", UI_SUITE_NOT_FOUND, status_code=404)
    ids = suite.case_ids_json if isinstance(suite.case_ids_json, list) else []
    if not ids:
        raise AppError("bad_request", UI_SUITE_EMPTY, status_code=400)
    env = None
    env_id = body.env_id if body.env_id is not None else suite.env_id
    if env_id is not None:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
    browsers = (
        [body.browser]
        if body.browser
        else (
            suite.browsers_json
            if isinstance(suite.browsers_json, list) and suite.browsers_json
            else ["chromium"]
        )
    )
    queue = (env.queue_name if env else "ui") or "ui"
    outs: list[UiRunOut] = []
    for browser in browsers:
        run = UiRun(
            project_id=project_id,
            trigger_type="suite",
            target_id=suite.id,
            env_id=env.id if env else None,
            browser=str(browser),
            account_name=body.account_name,
            queue_name=queue,
            status="pending",
            triggered_by=user.id,
            summary_json={"trigger": "ci"},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        task_id = enqueue_run_ui_case(run.id, queue=queue)
        if task_id:
            run.celery_task_id = task_id
            db.commit()
            db.refresh(run)
        outs.append(UiRunOut.model_validate(run))
    return outs


@router.get("/{run_id}", response_model=UiRunOut)
def get_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> UiRunOut:
    return UiRunOut.model_validate(_require_run(db, project_id, run_id))


@router.get("/{run_id}/steps", response_model=list[UiRunStepOut])
def list_run_steps(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[UiRunStepOut]:
    _require_run(db, project_id, run_id)
    rows = (
        db.query(UiRunStep)
        .filter(UiRunStep.run_id == run_id, UiRunStep.project_id == project_id)
        .order_by(UiRunStep.order, UiRunStep.id)
        .all()
    )
    return [UiRunStepOut.model_validate(r) for r in rows]


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
        db.query(UiRunStep)
        .filter(UiRunStep.run_id == run_id, UiRunStep.project_id == project_id)
        .order_by(UiRunStep.order, UiRunStep.id)
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
            headers={"Content-Disposition": f'attachment; filename="ui-run-{run_id}.html"'},
        )
    content = build_junit_xml(run_id=run.id, project_id=project_id, status=run.status, steps=steps)
    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ui-run-{run_id}.xml"'},
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
        db.query(UiRunStep)
        .filter(
            UiRunStep.id == step_id,
            UiRunStep.run_id == run_id,
            UiRunStep.project_id == project_id,
        )
        .first()
    )
    if step is None or not step.screenshot_key:
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    path: Path = absolute_path_for_storage_key(step.screenshot_key)
    if not path.is_file():
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    if not step.screenshot_key.startswith(f"{project_id}/"):
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    return FileResponse(path, media_type="image/png", filename=path.name)


def _fix_out(meta: dict) -> UiRunFixOut:
    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else None
    events = meta.get("events") if isinstance(meta.get("events"), list) else []
    return UiRunFixOut(
        id=str(meta["id"]),
        run_id=int(meta["run_id"]),
        case_id=meta.get("case_id"),
        status=str(meta.get("status") or ""),
        plan=UiRunFixPlan.model_validate(plan) if plan else None,
        current_script=str(meta.get("current_script") or ""),
        error_message=str(meta.get("error_message") or ""),
        patch_message=str(meta.get("patch_message") or ""),
        rerun_id=meta.get("rerun_id"),
        explore_session_id=meta.get("explore_session_id"),
        events=[e for e in events if isinstance(e, dict)],
    )


@router.post("/{run_id}/ai-fix", response_model=UiRunFixOut, status_code=201)
def start_run_fix(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiRunFixOut:
    run = _require_run(db, project_id, run_id)
    llm_cfg = get_llm_runtime(db)
    if not llm_cfg.api_key.strip():
        raise AppError("bad_request", "未配置 LLM API Key", status_code=400)
    meta = start_repair_explore(db, run, user_id=user.id, llm_cfg=llm_cfg)
    return _fix_out(meta)


@router.get("/{run_id}/ai-fix/{fix_id}", response_model=UiRunFixOut)
def get_run_fix(
    project_id: int,
    run_id: int,
    fix_id: str,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> UiRunFixOut:
    _require_run(db, project_id, run_id)
    meta = get_fix(fix_id)
    if meta is None or meta.get("run_id") != run_id or meta.get("project_id") != project_id:
        raise AppError("not_found", "修复会话不存在", status_code=404)
    return _fix_out(hydrate_fix(db, meta))


@router.post("/{run_id}/ai-fix/{fix_id}/confirm", response_model=UiRunFixOut)
def confirm_run_fix(
    project_id: int,
    run_id: int,
    fix_id: str,
    body: UiRunFixConfirm,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiRunFixOut:
    _require_run(db, project_id, run_id)
    meta = get_fix(fix_id)
    if meta is None or meta.get("run_id") != run_id or meta.get("project_id") != project_id:
        raise AppError("not_found", "修复会话不存在", status_code=404)
    if body.action == "cancel" and meta.get("status") in ("exploring", "awaiting_confirm"):
        return _fix_out(
            confirm_fix(
                db,
                meta,
                action="cancel",
                apply_patch=False,
                rerun=False,
                user_id=user.id,
            )
        )
    if meta.get("status") != "awaiting_confirm":
        raise AppError("bad_request", f"修复会话状态不可确认: {meta.get('status')}", status_code=400)
    updated = confirm_fix(
        db,
        meta,
        action=body.action,
        apply_patch=body.apply_patch,
        rerun=body.rerun,
        user_id=user.id,
    )
    return _fix_out(updated)
