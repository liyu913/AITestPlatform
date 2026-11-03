from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.ui_automation.ui_automation import UiCase, UiEnvironment, UiRun
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.ui_automation.ui_automation import (
    UiCaseCreate,
    UiCaseOut,
    UiCaseRunRequest,
    UiCaseUpdate,
    UiRunOut,
)
from app.services.functional.test_data_vars import as_str_dict
from app.services.ui_automation.ui_ai_explorer import steps_to_script
from app.services.ui_automation.ui_env_auth import normalize_auth_mode
from app.services.ui_automation.ui_run_executor import execute_ui_run
from app.tasks.ui_automation.run_ui_case import enqueue_run_ui_case
from app.user_messages import (
    MANUAL_CASE_NOT_IN_PROJECT,
    UI_CASE_NOT_FOUND,
    UI_ENV_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/ui-cases", tags=["ui-cases"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_case(db: Session, project_id: int, case_id: int) -> UiCase:
    row = db.query(UiCase).filter(UiCase.id == case_id, UiCase.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", UI_CASE_NOT_FOUND, status_code=404)
    return row


def _validate_manual_case(db: Session, project_id: int, manual_case_id: int | None) -> None:
    if manual_case_id is None:
        return
    c = db.get(TestCase, manual_case_id)
    if c is None or c.project_id != project_id:
        raise AppError("bad_request", MANUAL_CASE_NOT_IN_PROJECT, status_code=400)


def _dump_steps(items: list) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if hasattr(it, "model_dump"):
            out.append(it.model_dump(by_alias=True))
        elif isinstance(it, dict):
            out.append(it)
    return out


def _case_script(row: UiCase) -> str:
    script = row.script or ""
    if script.strip():
        return script
    steps = row.steps_json if isinstance(row.steps_json, list) else []
    if steps:
        return steps_to_script(steps)
    return ""


def _incoming_script(*, script: str | None, steps: list | None) -> str | None:
    """Prefer an explicit script. Recorder uploads only steps, which compile to Python."""
    if script is not None and str(script).strip():
        return script
    if steps:
        return steps_to_script(_dump_steps(steps))
    if script is not None:
        return script
    return None


def _case_out(row: UiCase) -> UiCaseOut:
    tags = row.tags_json if isinstance(row.tags_json, list) else None
    steps = row.steps_json if isinstance(row.steps_json, list) else []
    return UiCaseOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        script=_case_script(row),
        steps=steps,
        variables=as_str_dict(row.variables_json),
        auth_mode=normalize_auth_mode(getattr(row, "auth_mode", None)),
        account_name=(str(row.account_name).strip() if getattr(row, "account_name", None) else None) or None,
        manual_case_id=row.manual_case_id,
        status=row.status,
        tags_json=tags,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_options_payload(
    *,
    auth_mode: str | None,
    variables: dict[str, str] | None,
) -> dict | None:
    opts: dict = {}
    mode = normalize_auth_mode(auth_mode)
    # Store explicit modes including "none" (opt-out); omit when follow-env (None)
    if mode is not None:
        opts["auth_mode"] = mode
    vars_ = as_str_dict(variables) if variables else {}
    if vars_:
        opts["variables"] = vars_
    return opts or None


def _copy_manual_variables(db: Session, project_id: int, manual_case_id: int | None) -> dict[str, str]:
    if manual_case_id is None:
        return {}
    tc = db.get(TestCase, manual_case_id)
    if tc is None or tc.project_id != project_id:
        return {}
    return as_str_dict(tc.variables_json)


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


@router.get("/", response_model=Page[UiCaseOut])
def list_cases(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
    manual_case_id: int | None = Query(None, ge=1),
) -> Page[UiCaseOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(UiCase).filter(UiCase.project_id == project_id)
    if manual_case_id is not None:
        filtered = filtered.filter(UiCase.manual_case_id == manual_case_id)
    if search and search.strip():
        filtered = filtered.filter(UiCase.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(UiCase.updated_at.desc(), UiCase.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[UiCaseOut](items=[_case_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=UiCaseOut, status_code=201)
def create_case(
    project_id: int,
    body: UiCaseCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiCaseOut:
    _validate_manual_case(db, project_id, body.manual_case_id)
    variables = as_str_dict(body.variables)
    if not variables and body.manual_case_id is not None:
        variables = _copy_manual_variables(db, project_id, body.manual_case_id)
    dumped_steps = _dump_steps(body.steps) if body.steps else None
    row = UiCase(
        project_id=project_id,
        name=body.name.strip(),
        description=body.description,
        script=_incoming_script(script=body.script, steps=body.steps) or "",
        steps_json=dumped_steps or None,
        variables_json=variables or None,
        auth_mode=normalize_auth_mode(body.auth_mode),
        account_name=(body.account_name or "").strip() or None,
        manual_case_id=body.manual_case_id,
        status=body.status,
        tags_json=body.tags,
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _case_out(row)


@router.get("/{case_id}", response_model=UiCaseOut)
def get_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> UiCaseOut:
    return _case_out(_require_case(db, project_id, case_id))


@router.patch("/{case_id}", response_model=UiCaseOut)
def update_case(
    project_id: int,
    case_id: int,
    body: UiCaseUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiCaseOut:
    row = _require_case(db, project_id, case_id)
    data = body.model_dump(exclude_unset=True, by_alias=True)
    if "manual_case_id" in data:
        _validate_manual_case(db, project_id, data["manual_case_id"])
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    if "script" in data or "steps" in data:
        incoming = _incoming_script(
            script=body.script if "script" in data else None,
            steps=body.steps if "steps" in data else None,
        )
        if incoming is not None:
            row.script = incoming
        if "steps" in data:
            row.steps_json = _dump_steps(body.steps) if body.steps else None
        elif "script" in data:
            # Explicit script-only update: script is source of truth.
            row.steps_json = None
    if "manual_case_id" in data:
        row.manual_case_id = data["manual_case_id"]
    if "variables" in data:
        vars_ = as_str_dict(data["variables"])
        row.variables_json = vars_ or None
    if "auth_mode" in data:
        row.auth_mode = normalize_auth_mode(data["auth_mode"])
    if "account_name" in data:
        row.account_name = (str(data["account_name"]).strip() if data["account_name"] else "") or None
    if "status" in data and data["status"] is not None:
        row.status = data["status"]
    if "tags" in data:
        row.tags_json = data["tags"]
    db.commit()
    db.refresh(row)
    return _case_out(row)


@router.delete("/{case_id}", status_code=204)
def delete_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_case(db, project_id, case_id)
    db.delete(row)
    db.commit()


@router.post("/{case_id}/run", response_model=UiRunOut, status_code=201)
def run_case(
    project_id: int,
    case_id: int,
    body: UiCaseRunRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiRunOut:
    case = _require_case(db, project_id, case_id)
    env = _resolve_env(db, project_id, body.env_id)
    browser = body.browser or (env.browser if env else "chromium") or "chromium"
    queue = (env.queue_name if env else "ui") or "ui"
    # Request auth wins (including explicit "none"). When omitted, promote case
    # auth_mode including durable opt-out ("none" = skip env inject).
    if body.auth_mode is not None:
        auth_mode = normalize_auth_mode(body.auth_mode)
    else:
        case_auth = normalize_auth_mode(getattr(case, "auth_mode", None))
        auth_mode = case_auth if case_auth in ("none", "account", "token") else None
    if auth_mode == "account":
        account_name = (body.account_name or "").strip() or (
            getattr(case, "account_name", None) or None
        )
    else:
        # Do not inherit case account into follow-env / token / none (steals token inject)
        account_name = None
    run_opts = _run_options_payload(auth_mode=auth_mode, variables=body.variables)
    run = UiRun(
        project_id=project_id,
        trigger_type="case",
        target_id=case.id,
        env_id=env.id if env else None,
        browser=browser,
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
    return UiRunOut.model_validate(run)
