from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiCase, UiEnvironment, UiRun, UiSuite
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.ui_automation.ui_automation import (
    UiRunOut,
    UiSuiteCreate,
    UiSuiteOut,
    UiSuiteRunRequest,
    UiSuiteUpdate,
)
from app.services.functional.test_data_vars import as_str_dict
from app.services.ui_automation.ui_env_auth import normalize_auth_mode
from app.services.ui_automation.ui_run_executor import execute_ui_run
from app.tasks.ui_automation.run_ui_case import enqueue_run_ui_case
from app.user_messages import (
    UI_CASE_NOT_IN_PROJECT,
    UI_ENV_NOT_FOUND,
    UI_SUITE_EMPTY,
    UI_SUITE_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/ui-suites", tags=["ui-suites"])


def _require_suite(db: Session, project_id: int, suite_id: int) -> UiSuite:
    row = db.query(UiSuite).filter(UiSuite.id == suite_id, UiSuite.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", UI_SUITE_NOT_FOUND, status_code=404)
    return row


def _validate_case_ids(db: Session, project_id: int, case_ids: list[int]) -> list[int]:
    out: list[int] = []
    for cid in case_ids:
        c = db.query(UiCase).filter(UiCase.id == cid, UiCase.project_id == project_id).first()
        if c is None:
            raise AppError("bad_request", UI_CASE_NOT_IN_PROJECT, detail=str(cid), status_code=400)
        out.append(cid)
    return out


def _resolve_env(db: Session, project_id: int, env_id: int | None) -> UiEnvironment | None:
    if env_id is not None:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
        return env
    return (
        db.query(UiEnvironment)
        .filter(UiEnvironment.project_id == project_id, UiEnvironment.is_default.is_(True))
        .first()
    )


def _suite_out(row: UiSuite) -> UiSuiteOut:
    ids = row.case_ids_json if isinstance(row.case_ids_json, list) else []
    browsers = row.browsers_json if isinstance(row.browsers_json, list) else []
    return UiSuiteOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        case_ids=[int(x) for x in ids],
        stop_on_fail=bool(row.stop_on_fail),
        env_id=row.env_id,
        browsers=[str(b) for b in browsers],
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=Page[UiSuiteOut])
def list_suites(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[UiSuiteOut]:
    p, ps = max(1, page), min(100, max(1, page_size))
    filtered = db.query(UiSuite).filter(UiSuite.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(UiSuite.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(UiSuite.updated_at.desc(), UiSuite.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[UiSuiteOut](items=[_suite_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=UiSuiteOut, status_code=201)
def create_suite(
    project_id: int,
    body: UiSuiteCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiSuiteOut:
    case_ids = _validate_case_ids(db, project_id, body.case_ids)
    if body.env_id is not None:
        _resolve_env(db, project_id, body.env_id)
    row = UiSuite(
        project_id=project_id,
        name=body.name.strip(),
        description=body.description,
        case_ids_json=case_ids,
        stop_on_fail=body.stop_on_fail,
        env_id=body.env_id,
        browsers_json=list(body.browsers) if body.browsers else [],
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _suite_out(row)


@router.get("/{suite_id}", response_model=UiSuiteOut)
def get_suite(
    project_id: int,
    suite_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> UiSuiteOut:
    return _suite_out(_require_suite(db, project_id, suite_id))


@router.patch("/{suite_id}", response_model=UiSuiteOut)
def update_suite(
    project_id: int,
    suite_id: int,
    body: UiSuiteUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiSuiteOut:
    row = _require_suite(db, project_id, suite_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    if "case_ids" in data:
        row.case_ids_json = _validate_case_ids(db, project_id, data["case_ids"] or [])
    if "stop_on_fail" in data and data["stop_on_fail"] is not None:
        row.stop_on_fail = bool(data["stop_on_fail"])
    if "env_id" in data:
        if data["env_id"] is not None:
            _resolve_env(db, project_id, data["env_id"])
        row.env_id = data["env_id"]
    if "browsers" in data:
        row.browsers_json = data["browsers"] or []
    db.commit()
    db.refresh(row)
    return _suite_out(row)


@router.delete("/{suite_id}", status_code=204)
def delete_suite(
    project_id: int,
    suite_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_suite(db, project_id, suite_id)
    db.delete(row)
    db.commit()


@router.post("/{suite_id}/run", response_model=list[UiRunOut], status_code=201)
def run_suite(
    project_id: int,
    suite_id: int,
    body: UiSuiteRunRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[UiRunOut]:
    suite = _require_suite(db, project_id, suite_id)
    ids = suite.case_ids_json if isinstance(suite.case_ids_json, list) else []
    if not ids:
        raise AppError("bad_request", UI_SUITE_EMPTY, status_code=400)
    env = _resolve_env(db, project_id, body.env_id if body.env_id is not None else suite.env_id)
    browsers = (
        [body.browser]
        if body.browser
        else (
            suite.browsers_json
            if isinstance(suite.browsers_json, list) and suite.browsers_json
            else (
                env.browsers_json
                if env and isinstance(env.browsers_json, list) and env.browsers_json
                else [env.browser if env else "chromium"]
            )
        )
    )
    queue = (env.queue_name if env else "ui") or "ui"
    auth_mode = normalize_auth_mode(body.auth_mode)
    account_name = (
        (body.account_name or "").strip() or None if auth_mode == "account" else None
    )
    run_opts: dict = {}
    # Store explicit modes including "none"; omit when follow-env (None)
    if auth_mode is not None:
        run_opts["auth_mode"] = auth_mode
    vars_ = as_str_dict(body.variables) if body.variables else {}
    if vars_:
        run_opts["variables"] = vars_
    outs: list[UiRunOut] = []
    for browser in browsers:
        run = UiRun(
            project_id=project_id,
            trigger_type="suite",
            target_id=suite.id,
            env_id=env.id if env else None,
            browser=str(browser),
            headless=body.headless,
            account_name=account_name,
            queue_name=queue,
            status="pending",
            triggered_by=user.id,
            summary_json={"run_options": run_opts} if run_opts else None,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        if body.sync:
            execute_ui_run(db, run.id)
            db.refresh(run)
        else:
            task_id = enqueue_run_ui_case(run.id, queue=queue)
            if task_id:
                run.celery_task_id = task_id
                db.commit()
                db.refresh(run)
        outs.append(UiRunOut.model_validate(run))
    return outs
