from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase, ApiEnvironment
from app.models.perf.perf_automation import PerfRun, PerfScenario
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.perf.perf_automation import (
    PerfCaseItem,
    PerfScenarioCreate,
    PerfScenarioOut,
    PerfScenarioRunRequest,
    PerfScenarioUpdate,
    PerfSlaIn,
    PerfRunOut,
    normalize_case_items,
    sla_dict,
)
from app.services.perf.perf_run_executor import execute_perf_run
from app.tasks.perf.run_perf_scenario import enqueue_run_perf_scenario
from app.user_messages import (
    API_CASE_NOT_IN_PROJECT,
    API_ENV_NOT_FOUND,
    PERF_SCENARIO_EMPTY,
    PERF_SCENARIO_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/perf-scenarios", tags=["perf-scenarios"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_scenario(db: Session, project_id: int, scenario_id: int) -> PerfScenario:
    row = (
        db.query(PerfScenario)
        .filter(PerfScenario.id == scenario_id, PerfScenario.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", PERF_SCENARIO_NOT_FOUND, status_code=404)
    return row


def _validate_case_items(db: Session, project_id: int, items: list[PerfCaseItem] | list[dict]) -> list[dict]:
    normalized = normalize_case_items(items)
    if not normalized:
        raise AppError("bad_request", PERF_SCENARIO_EMPTY, status_code=400)
    for it in normalized:
        c = (
            db.query(ApiCase)
            .filter(ApiCase.id == it["case_id"], ApiCase.project_id == project_id)
            .first()
        )
        if c is None:
            raise AppError("bad_request", API_CASE_NOT_IN_PROJECT, detail=str(it["case_id"]), status_code=400)
    return normalized


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


def _scenario_out(row: PerfScenario) -> PerfScenarioOut:
    items_raw = row.case_items_json if isinstance(row.case_items_json, list) else []
    items = [PerfCaseItem.model_validate(x) for x in normalize_case_items(items_raw)]
    sla_raw = row.sla_json if isinstance(row.sla_json, dict) else {}
    return PerfScenarioOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        env_id=row.env_id,
        case_items=items,
        users=int(row.users or 10),
        spawn_rate=float(row.spawn_rate or 2.0),
        run_time_sec=int(row.run_time_sec or 60),
        think_time_ms=int(row.think_time_ms or 0),
        sla=PerfSlaIn.model_validate(sla_raw),
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=Page[PerfScenarioOut])
def list_scenarios(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[PerfScenarioOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(PerfScenario).filter(PerfScenario.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(PerfScenario.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(PerfScenario.updated_at.desc(), PerfScenario.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[PerfScenarioOut](items=[_scenario_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=PerfScenarioOut, status_code=201)
def create_scenario(
    project_id: int,
    body: PerfScenarioCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> PerfScenarioOut:
    case_items = _validate_case_items(db, project_id, body.case_items)
    env = _resolve_env(db, project_id, body.env_id)
    row = PerfScenario(
        project_id=project_id,
        name=body.name.strip(),
        description=body.description or "",
        env_id=env.id if env else body.env_id,
        case_items_json=case_items,
        users=body.users,
        spawn_rate=body.spawn_rate,
        run_time_sec=body.run_time_sec,
        think_time_ms=body.think_time_ms,
        sla_json=sla_dict(body.sla),
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _scenario_out(row)


@router.get("/{scenario_id}", response_model=PerfScenarioOut)
def get_scenario(
    project_id: int,
    scenario_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> PerfScenarioOut:
    return _scenario_out(_require_scenario(db, project_id, scenario_id))


@router.patch("/{scenario_id}", response_model=PerfScenarioOut)
def update_scenario(
    project_id: int,
    scenario_id: int,
    body: PerfScenarioUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> PerfScenarioOut:
    row = _require_scenario(db, project_id, scenario_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = data["name"].strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    if "case_items" in data and data["case_items"] is not None:
        row.case_items_json = _validate_case_items(db, project_id, body.case_items or [])
    if "env_id" in data:
        if data["env_id"] is None:
            row.env_id = None
        else:
            env = _resolve_env(db, project_id, data["env_id"])
            row.env_id = env.id if env else None
    for field in ("users", "spawn_rate", "run_time_sec", "think_time_ms"):
        if field in data and data[field] is not None:
            setattr(row, field, data[field])
    if "sla" in data and body.sla is not None:
        row.sla_json = sla_dict(body.sla)
    db.commit()
    db.refresh(row)
    return _scenario_out(row)


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(
    project_id: int,
    scenario_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_scenario(db, project_id, scenario_id)
    db.delete(row)
    db.commit()


@router.post("/{scenario_id}/run", response_model=PerfRunOut)
def run_scenario(
    project_id: int,
    scenario_id: int,
    body: PerfScenarioRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> PerfRunOut:
    scenario = _require_scenario(db, project_id, scenario_id)
    case_items = normalize_case_items(scenario.case_items_json if isinstance(scenario.case_items_json, list) else [])
    if not case_items:
        raise AppError("bad_request", PERF_SCENARIO_EMPTY, status_code=400)

    env_id = body.env_id if body.env_id is not None else scenario.env_id
    env = _resolve_env(db, project_id, env_id)

    users = body.users if body.users is not None else int(scenario.users or 10)
    spawn_rate = body.spawn_rate if body.spawn_rate is not None else float(scenario.spawn_rate or 2.0)
    run_time_sec = body.run_time_sec if body.run_time_sec is not None else int(scenario.run_time_sec or 60)

    config = {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "case_items": case_items,
        "users": users,
        "spawn_rate": spawn_rate,
        "run_time_sec": run_time_sec,
        "think_time_ms": int(scenario.think_time_ms or 0),
        "sla": scenario.sla_json if isinstance(scenario.sla_json, dict) else {},
        "base_url": env.base_url if env else None,
    }

    run = PerfRun(
        project_id=project_id,
        scenario_id=scenario.id,
        env_id=env.id if env else None,
        status="pending",
        config_snapshot_json=config,
        triggered_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    if body.sync:
        execute_perf_run(db, run.id)
        db.refresh(run)
    else:
        task_id = enqueue_run_perf_scenario(run.id)
        if task_id:
            run.celery_task_id = task_id
            db.commit()
            db.refresh(run)
    return PerfRunOut.model_validate(run)
