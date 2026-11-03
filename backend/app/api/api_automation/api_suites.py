from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase, ApiEnvironment, ApiRun, ApiSuite
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.api_automation.api_automation import (
    ApiRunOut,
    ApiSuiteCreate,
    ApiSuiteOut,
    ApiSuiteRunRequest,
    ApiSuiteUpdate,
    ScenarioEditorReviseIn,
    ScenarioEditorReviseOut,
)
from app.schemas.page import Page
from app.services.ai.effective_ai_settings import get_llm_runtime
from app.services.api_automation.api_run_executor import execute_api_run
from app.services.api_automation.api_scenario_editor import revise_scenario_steps
from app.services.api_automation.api_suite_steps import case_ids_from_steps, normalize_steps, validate_steps_graph
from app.tasks.api_automation.run_api_case import enqueue_run_api_case
from app.user_messages import (
    API_CASE_NOT_IN_PROJECT,
    API_ENV_NOT_FOUND,
    API_SUITE_EMPTY,
    API_SUITE_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/api-suites", tags=["api-suites"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_suite(db: Session, project_id: int, suite_id: int) -> ApiSuite:
    row = (
        db.query(ApiSuite)
        .filter(ApiSuite.id == suite_id, ApiSuite.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", API_SUITE_NOT_FOUND, status_code=404)
    return row


def _validate_case_ids(db: Session, project_id: int, case_ids: list[int]) -> list[int]:
    out: list[int] = []
    for cid in case_ids:
        c = db.query(ApiCase).filter(ApiCase.id == cid, ApiCase.project_id == project_id).first()
        if c is None:
            raise AppError("bad_request", API_CASE_NOT_IN_PROJECT, detail=str(cid), status_code=400)
        out.append(cid)
    return out


def _resolve_env(db: Session, project_id: int, env_id: int | None) -> ApiEnvironment | None:
    if env_id is not None:
        env = (
            db.query(ApiEnvironment)
            .filter(ApiEnvironment.id == env_id, ApiEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("not_found", API_ENV_NOT_FOUND, status_code=404)
        return env
    return (
        db.query(ApiEnvironment)
        .filter(ApiEnvironment.project_id == project_id, ApiEnvironment.is_default.is_(True))
        .first()
    )


def _suite_out(row: ApiSuite) -> ApiSuiteOut:
    case_ids = row.case_ids_json if isinstance(row.case_ids_json, list) else []
    steps = normalize_steps(
        row.steps_json if isinstance(row.steps_json, list) else None,
        [int(x) for x in case_ids],
        stop_on_fail=bool(row.stop_on_fail),
    )
    derived_ids = case_ids_from_steps(steps) if steps else [int(x) for x in case_ids]
    return ApiSuiteOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        case_ids=derived_ids,
        steps=steps,
        stop_on_fail=bool(row.stop_on_fail),
        env_id=row.env_id,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_steps_payload(
    db: Session,
    project_id: int,
    *,
    case_ids: list[int] | None,
    steps_in: list | None,
    stop_on_fail: bool,
) -> tuple[list[int], list[dict]]:
    if steps_in is not None:
        raw = [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in steps_in]
        steps = normalize_steps(raw, None, stop_on_fail=stop_on_fail)
        errs = validate_steps_graph(steps)
        if errs:
            raise AppError("bad_request", "; ".join(errs), status_code=400)
        ids = case_ids_from_steps(steps)
        if ids:
            _validate_case_ids(db, project_id, ids)
        return ids, steps
    ids = _validate_case_ids(db, project_id, case_ids or [])
    steps = normalize_steps(None, ids, stop_on_fail=stop_on_fail)
    return ids, steps


@router.get("/", response_model=Page[ApiSuiteOut])
def list_suites(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[ApiSuiteOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(ApiSuite).filter(ApiSuite.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(ApiSuite.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(ApiSuite.updated_at.desc(), ApiSuite.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[ApiSuiteOut](items=[_suite_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=ApiSuiteOut, status_code=201)
def create_suite(
    project_id: int,
    body: ApiSuiteCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiSuiteOut:
    if body.env_id is not None:
        _resolve_env(db, project_id, body.env_id)
    case_ids, steps = _apply_steps_payload(
        db,
        project_id,
        case_ids=body.case_ids,
        steps_in=body.steps,
        stop_on_fail=body.stop_on_fail,
    )
    row = ApiSuite(
        project_id=project_id,
        name=body.name.strip(),
        description=body.description,
        case_ids_json=case_ids,
        steps_json=steps,
        stop_on_fail=body.stop_on_fail,
        env_id=body.env_id,
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _suite_out(row)


@router.get("/{suite_id}", response_model=ApiSuiteOut)
def get_suite(
    project_id: int,
    suite_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ApiSuiteOut:
    return _suite_out(_require_suite(db, project_id, suite_id))


@router.patch("/{suite_id}", response_model=ApiSuiteOut)
def update_suite(
    project_id: int,
    suite_id: int,
    body: ApiSuiteUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiSuiteOut:
    row = _require_suite(db, project_id, suite_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    if "stop_on_fail" in data and data["stop_on_fail"] is not None:
        row.stop_on_fail = bool(data["stop_on_fail"])
    if "env_id" in data:
        if data["env_id"] is not None:
            _resolve_env(db, project_id, data["env_id"])
        row.env_id = data["env_id"]
    if "steps" in data or "case_ids" in data:
        case_ids, steps = _apply_steps_payload(
            db,
            project_id,
            case_ids=data.get("case_ids") if "case_ids" in data else (
                row.case_ids_json if isinstance(row.case_ids_json, list) else []
            ),
            steps_in=body.steps if "steps" in data else None,
            stop_on_fail=bool(row.stop_on_fail),
        )
        # if only case_ids updated without steps key, rebuild linear steps
        if "steps" not in data and "case_ids" in data:
            case_ids, steps = _apply_steps_payload(
                db,
                project_id,
                case_ids=data.get("case_ids") or [],
                steps_in=None,
                stop_on_fail=bool(row.stop_on_fail),
            )
        row.case_ids_json = case_ids
        row.steps_json = steps
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


@router.post("/{suite_id}/agent/revise", response_model=ScenarioEditorReviseOut)
def revise_suite_with_agent(
    project_id: int,
    suite_id: int,
    body: ScenarioEditorReviseIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ScenarioEditorReviseOut:
    """Propose a full step revision. Does not persist; the editor applies it after confirm."""
    _require_suite(db, project_id, suite_id)
    result = revise_scenario_steps(
        db,
        project_id=project_id,
        message=body.message,
        steps=[step.model_dump() for step in body.steps],
        messages=[turn.model_dump() for turn in body.messages],
        llm_cfg=get_llm_runtime(db),
    )
    return ScenarioEditorReviseOut.model_validate(result)


@router.post("/{suite_id}/run", response_model=ApiRunOut, status_code=201)
def run_suite(
    project_id: int,
    suite_id: int,
    body: ApiSuiteRunRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiRunOut:
    suite = _require_suite(db, project_id, suite_id)
    ids = suite.case_ids_json if isinstance(suite.case_ids_json, list) else []
    steps = suite.steps_json if isinstance(suite.steps_json, list) else []
    if not ids and not steps:
        raise AppError("bad_request", API_SUITE_EMPTY, status_code=400)
    env = _resolve_env(db, project_id, body.env_id if body.env_id is not None else suite.env_id)
    run = ApiRun(
        project_id=project_id,
        trigger_type="suite",
        target_id=suite.id,
        env_id=env.id if env else None,
        status="pending",
        triggered_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if body.sync:
        execute_api_run(db, run.id)
        db.refresh(run)
    else:
        task_id = enqueue_run_api_case(run.id)
        if task_id:
            run.celery_task_id = task_id
            db.commit()
            db.refresh(run)
    return ApiRunOut.model_validate(run)
