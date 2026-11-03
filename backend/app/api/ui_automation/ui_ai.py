"""API for UI AI explore → generate final UiCase."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.ui_automation.ui_automation import UiAiExploreJob, UiCase, UiEnvironment
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.ui_automation.ui_ai_explore import (
    UiAiExploreConfirmSave,
    UiAiExploreCreate,
    UiAiExploreJobOut,
)
from app.schemas.ui_automation.ui_automation import UiCaseOut
from app.services.ui_automation.ui_ai_explorer import steps_to_script
from app.tasks.ui_automation.run_ui_ai_explore import enqueue_ui_ai_explore
from app.user_messages import (
    TEST_CASE_NOT_FOUND,
    UI_AI_JOB_CANCELLED,
    UI_AI_JOB_NO_STEPS,
    UI_AI_JOB_NOT_FOUND,
    UI_AI_JOB_NOT_READY,
    UI_ENV_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/ui-ai", tags=["ui-ai"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _job_out(row: UiAiExploreJob) -> UiAiExploreJobOut:
    steps = row.result_steps_json if isinstance(row.result_steps_json, list) else []
    return UiAiExploreJobOut(
        id=row.id,
        project_id=row.project_id,
        testcase_id=row.testcase_id,
        env_id=row.env_id,
        account_name=row.account_name,
        browser=row.browser or "chromium",
        status=row.status,
        plan_json=row.plan_json if isinstance(row.plan_json, (dict, list)) else None,
        trace_json=row.trace_json if isinstance(row.trace_json, list) else None,
        result_steps=steps,
        playwright_code=row.playwright_code or "",
        dry_run_status=row.dry_run_status,
        dry_run_error=row.dry_run_error or "",
        error_message=row.error_message or "",
        saved_ui_case_id=row.saved_ui_case_id,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require_job(db: Session, project_id: int, job_id: int) -> UiAiExploreJob:
    row = (
        db.query(UiAiExploreJob)
        .filter(UiAiExploreJob.id == job_id, UiAiExploreJob.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", UI_AI_JOB_NOT_FOUND, status_code=404)
    return row


def _resolve_env(db: Session, project_id: int, env_id: int | None) -> UiEnvironment:
    if env_id is not None:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
        return env
    env = (
        db.query(UiEnvironment)
        .filter(UiEnvironment.project_id == project_id, UiEnvironment.is_default.is_(True))
        .first()
    )
    if env is None:
        env = db.query(UiEnvironment).filter(UiEnvironment.project_id == project_id).first()
    if env is None:
        raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
    return env


@router.get("/explore-jobs", response_model=Page[UiAiExploreJobOut])
def list_jobs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page[UiAiExploreJobOut]:
    p, ps = _clamp_pagination(page, page_size)
    q = db.query(UiAiExploreJob).filter(UiAiExploreJob.project_id == project_id)
    total = q.count()
    rows = q.order_by(UiAiExploreJob.updated_at.desc(), UiAiExploreJob.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[UiAiExploreJobOut](
        items=[_job_out(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/explore-jobs", response_model=UiAiExploreJobOut, status_code=201)
def create_job(
    project_id: int,
    body: UiAiExploreCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiAiExploreJobOut:
    tc = (
        db.query(TestCase)
        .filter(TestCase.id == body.testcase_id, TestCase.project_id == project_id)
        .first()
    )
    if tc is None:
        raise AppError("not_found", TEST_CASE_NOT_FOUND, status_code=404)
    env = _resolve_env(db, project_id, body.env_id)
    if body.account_name:
        accounts = env.accounts_json if isinstance(env.accounts_json, list) else []
        names = {str(a.get("name")) for a in accounts if isinstance(a, dict)}
        if body.account_name not in names:
            raise AppError("bad_request", "环境账号不存在", status_code=400)

    job = UiAiExploreJob(
        project_id=project_id,
        testcase_id=body.testcase_id,
        env_id=env.id,
        account_name=body.account_name,
        browser=body.browser,
        status="pending",
        plan_json={"_max_steps": body.max_steps},
        creator_id=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    queue = env.queue_name or "ui"
    try:
        task_id = enqueue_ui_ai_explore(job.id, queue=queue)
        if task_id:
            job.celery_task_id = task_id
            db.commit()
            db.refresh(job)
    except Exception:
        # keep pending; worker may pick up later if requeued manually
        pass
    return _job_out(job)


@router.get("/explore-jobs/{job_id}", response_model=UiAiExploreJobOut)
def get_job(
    project_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> UiAiExploreJobOut:
    return _job_out(_require_job(db, project_id, job_id))


@router.post("/explore-jobs/{job_id}/cancel", response_model=UiAiExploreJobOut)
def cancel_job(
    project_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiAiExploreJobOut:
    job = _require_job(db, project_id, job_id)
    if job.status in ("succeeded", "failed", "cancelled"):
        return _job_out(job)
    job.status = "cancelled"
    job.error_message = UI_AI_JOB_CANCELLED
    db.commit()
    db.refresh(job)
    return _job_out(job)


@router.post("/explore-jobs/{job_id}/confirm-save", response_model=UiCaseOut, status_code=201)
def confirm_save(
    project_id: int,
    job_id: int,
    body: UiAiExploreConfirmSave,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiCaseOut:
    job = _require_job(db, project_id, job_id)
    if job.status not in ("succeeded", "failed"):
        raise AppError("bad_request", UI_AI_JOB_NOT_READY, status_code=400)

    script = (body.script or "").strip()
    if not script:
        script = (job.playwright_code or "").strip()
    if not script:
        if body.steps is not None:
            steps = [s.model_dump(by_alias=True) for s in body.steps]
        else:
            steps = job.result_steps_json if isinstance(job.result_steps_json, list) else []
        script = steps_to_script(steps).strip()
    if not script:
        raise AppError("bad_request", UI_AI_JOB_NO_STEPS, status_code=400)

    tc = db.get(TestCase, job.testcase_id)
    default_name = f"{tc.title} (AI)" if tc else f"AI UI case #{job.id}"
    name = (body.name or "").strip() or default_name

    case = UiCase(
        project_id=project_id,
        name=name[:512],
        description=f"由 UI AI 探索任务 #{job.id} 生成",
        script=script,
        steps_json=None,
        manual_case_id=job.testcase_id,
        status=body.status,
        creator_id=user.id,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    job.saved_ui_case_id = case.id
    db.commit()

    tags = case.tags_json if isinstance(case.tags_json, list) else None
    return UiCaseOut(
        id=case.id,
        project_id=case.project_id,
        name=case.name,
        description=case.description or "",
        script=script,
        steps=[],
        manual_case_id=case.manual_case_id,
        status=case.status,
        tags_json=tags,
        creator_id=case.creator_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )
