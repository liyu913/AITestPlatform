"""Functional test case execution history (agent_once / future manual)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.testcase import TestCase
from app.models.functional.testcase_run import TestCaseRun
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.models.ui_automation.ui_automation import UiScenarioAgentSession
from app.schemas.functional.testcase_run import TestCaseRunOut
from app.schemas.page import Page
from app.services.file_storage import absolute_path_for_storage_key
from app.services.functional.testcase_run_finalize import (
    allowed_screenshot_keys,
    enrich_summary_step_screenshots,
)
from app.services.ui_automation.ui_scenario_agent import MANUAL_STOP_MESSAGE, stop_session
from app.user_messages import TESTCASE_RUN_NOT_CANCELLABLE, UI_SCREENSHOT_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/test-case-runs", tags=["test-case-runs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_run(db: Session, project_id: int, run_id: int) -> TestCaseRun:
    row = (
        db.query(TestCaseRun)
        .filter(TestCaseRun.id == run_id, TestCaseRun.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", "功能用例执行记录不存在", status_code=404)
    return row


def _run_out(row: TestCaseRun, case_title: str = "", *, enrich_shots: bool = False) -> TestCaseRunOut:
    summary = row.summary_json if isinstance(row.summary_json, dict) else None
    if enrich_shots:
        summary = enrich_summary_step_screenshots(summary)
    return TestCaseRunOut(
        id=row.id,
        project_id=row.project_id,
        case_id=row.case_id,
        case_title=case_title,
        triggered_by=row.triggered_by,
        mode=row.mode or "agent_once",
        status=row.status or "pending",
        env_id=row.env_id,
        session_id=row.session_id,
        summary=row.summary or "",
        error_message=row.error_message or "",
        actual_result=row.actual_result or "",
        summary_json=summary,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _titles_for(db: Session, case_ids: list[int]) -> dict[int, str]:
    if not case_ids:
        return {}
    rows = db.query(TestCase.id, TestCase.title).filter(TestCase.id.in_(case_ids)).all()
    return {int(r.id): str(r.title or "") for r in rows}


@router.get("/", response_model=Page[TestCaseRunOut])
def list_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    case_id: int | None = Query(None),
    status: str | None = Query(None, max_length=32),
    mode: str | None = Query(None, max_length=32),
) -> Page[TestCaseRunOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(TestCaseRun).filter(TestCaseRun.project_id == project_id)
    if case_id is not None:
        filtered = filtered.filter(TestCaseRun.case_id == case_id)
    if status:
        filtered = filtered.filter(TestCaseRun.status == status)
    if mode:
        filtered = filtered.filter(TestCaseRun.mode == mode)
    total = filtered.count()
    rows = (
        filtered.order_by(TestCaseRun.updated_at.desc(), TestCaseRun.id.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )
    titles = _titles_for(db, [r.case_id for r in rows])
    return Page[TestCaseRunOut](
        items=[_run_out(r, titles.get(r.case_id, "")) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/{run_id}", response_model=TestCaseRunOut)
def get_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    __: User = Depends(get_current_user),
) -> TestCaseRunOut:
    row = _require_run(db, project_id, run_id)
    titles = _titles_for(db, [row.case_id])
    return _run_out(row, titles.get(row.case_id, ""), enrich_shots=True)


def _mark_run_cancelled(row: TestCaseRun) -> None:
    row.status = "cancelled"
    row.error_message = MANUAL_STOP_MESSAGE
    row.summary = MANUAL_STOP_MESSAGE
    row.actual_result = MANUAL_STOP_MESSAGE
    row.finished_at = now_beijing()
    if row.started_at is None:
        row.started_at = row.finished_at


@router.post("/{run_id}/cancel", response_model=TestCaseRunOut)
def cancel_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    __: User = Depends(get_current_user),
) -> TestCaseRunOut:
    row = _require_run(db, project_id, run_id)
    if row.status not in ("pending", "running"):
        raise AppError("bad_request", TESTCASE_RUN_NOT_CANCELLABLE, status_code=400)

    session: UiScenarioAgentSession | None = None
    if row.session_id is not None:
        session = db.get(UiScenarioAgentSession, row.session_id)
        if session is not None and session.project_id != project_id:
            session = None

    if session is not None and session.status in ("running", "awaiting_human"):
        stop_session(db, session)
        db.refresh(row)
    else:
        _mark_run_cancelled(row)
        db.commit()
        db.refresh(row)

    titles = _titles_for(db, [row.case_id])
    return _run_out(row, titles.get(row.case_id, ""))


@router.get("/{run_id}/screenshots")
def get_run_screenshot(
    project_id: int,
    run_id: int,
    key: str = Query(..., min_length=1, max_length=1024),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    __: User = Depends(get_current_user),
) -> FileResponse:
    row = _require_run(db, project_id, run_id)
    summary = row.summary_json if isinstance(row.summary_json, dict) else {}
    allowed = allowed_screenshot_keys(summary)
    norm = key.replace("\\", "/")
    if ".." in norm or key not in allowed:
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    # Must stay under this project's explore/run storage prefix
    if not (
        norm.startswith(f"{project_id}/ui-explore/")
        or norm.startswith(f"{project_id}/test-case-runs/")
        or norm.startswith(f"{project_id}/ui-runs/")
    ):
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    path: Path = absolute_path_for_storage_key(key)
    if not path.is_file():
        raise AppError("not_found", UI_SCREENSHOT_NOT_FOUND, status_code=404)
    return FileResponse(path, media_type="image/png", filename=path.name)
