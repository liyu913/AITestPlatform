from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiEnvironment
from app.models.project.project import ProjectMember
from app.schemas.api_automation.api_automation import ApiEnvCreate, ApiEnvOut, ApiEnvUpdate
from app.schemas.page import Page
from app.services.api_automation import api_env_secrets
from app.user_messages import API_ENV_NOT_FOUND, API_ENV_SECRETS_ENCRYPT_FAILED

router = APIRouter(prefix="/projects/{project_id}/api-envs", tags=["api-envs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_env(db: Session, project_id: int, env_id: int) -> ApiEnvironment:
    row = (
        db.query(ApiEnvironment)
        .filter(ApiEnvironment.id == env_id, ApiEnvironment.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", API_ENV_NOT_FOUND, status_code=404)
    return row


def _as_str_dict(data: dict | None) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _env_out(row: ApiEnvironment) -> ApiEnvOut:
    return ApiEnvOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        base_url=row.base_url,
        variables=_as_str_dict(row.variables_json),
        secret_keys=api_env_secrets.secret_keys(row.secrets_encrypted),
        headers=_as_str_dict(row.headers_json),
        is_default=bool(row.is_default),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _clear_other_defaults(db: Session, project_id: int, keep_id: int | None = None) -> None:
    q = db.query(ApiEnvironment).filter(
        ApiEnvironment.project_id == project_id,
        ApiEnvironment.is_default.is_(True),
    )
    if keep_id is not None:
        q = q.filter(ApiEnvironment.id != keep_id)
    for row in q.all():
        row.is_default = False


def _try_encode_secrets(secrets: dict[str, str]) -> str | None:
    try:
        return api_env_secrets.encode_secrets(secrets)
    except ValueError as ex:
        raise AppError("bad_request", API_ENV_SECRETS_ENCRYPT_FAILED, detail=str(ex), status_code=400) from ex


@router.get("/", response_model=Page[ApiEnvOut])
def list_envs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[ApiEnvOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(ApiEnvironment).filter(ApiEnvironment.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(ApiEnvironment.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(ApiEnvironment.updated_at.desc(), ApiEnvironment.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[ApiEnvOut](items=[_env_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=ApiEnvOut, status_code=201)
def create_env(
    project_id: int,
    body: ApiEnvCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiEnvOut:
    if body.is_default:
        _clear_other_defaults(db, project_id)
    row = ApiEnvironment(
        project_id=project_id,
        name=body.name.strip(),
        base_url=body.base_url,
        variables_json=body.variables,
        secrets_encrypted=_try_encode_secrets(body.secrets),
        headers_json=body.headers,
        is_default=body.is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _env_out(row)


@router.get("/{env_id}", response_model=ApiEnvOut)
def get_env(
    project_id: int,
    env_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ApiEnvOut:
    return _env_out(_require_env(db, project_id, env_id))


@router.patch("/{env_id}", response_model=ApiEnvOut)
def update_env(
    project_id: int,
    env_id: int,
    body: ApiEnvUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiEnvOut:
    row = _require_env(db, project_id, env_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "base_url" in data and data["base_url"] is not None:
        row.base_url = data["base_url"]
    if "variables" in data:
        row.variables_json = data["variables"] or {}
    if "headers" in data:
        row.headers_json = data["headers"] or {}
    if "secrets" in data:
        row.secrets_encrypted = _try_encode_secrets(data["secrets"] or {})
    if data.get("is_default") is True:
        _clear_other_defaults(db, project_id, keep_id=row.id)
        row.is_default = True
    elif data.get("is_default") is False:
        row.is_default = False
    db.commit()
    db.refresh(row)
    return _env_out(row)


@router.delete("/{env_id}", status_code=204)
def delete_env(
    project_id: int,
    env_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_env(db, project_id, env_id)
    db.delete(row)
    db.commit()
