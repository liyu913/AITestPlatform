from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.errors import AppError
from app.models.app_automation.app_automation import AppEnvironment
from app.models.project.project import ProjectMember
from app.schemas.app_automation.app_automation import AppEnvCreate, AppEnvOut, AppEnvUpdate
from app.schemas.page import Page
from app.services.api_automation import api_env_secrets
from app.user_messages import APP_ENV_NOT_FOUND, APP_ENV_SECRETS_ENCRYPT_FAILED

router = APIRouter(prefix="/projects/{project_id}/app-envs", tags=["app-envs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_env(db: Session, project_id: int, env_id: int) -> AppEnvironment:
    row = (
        db.query(AppEnvironment)
        .filter(AppEnvironment.id == env_id, AppEnvironment.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", APP_ENV_NOT_FOUND, status_code=404)
    return row


def _as_str_dict(data: dict | None) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _accounts(data: list | None) -> list[dict]:
    if not isinstance(data, list):
        return []
    out = []
    for a in data:
        if isinstance(a, dict) and a.get("name"):
            out.append(
                {
                    "name": str(a["name"]),
                    "variables": _as_str_dict(a.get("variables") if isinstance(a.get("variables"), dict) else {}),
                }
            )
    return out


def _env_out(row: AppEnvironment) -> AppEnvOut:
    return AppEnvOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        platform=row.platform or "android",
        appium_server_url=row.appium_server_url or "http://127.0.0.1:4723",
        device_name=row.device_name or "",
        udid=row.udid or "",
        platform_version=row.platform_version or "",
        automation_name=row.automation_name or "UiAutomator2",
        app_package=row.app_package or "",
        app_activity=row.app_activity or "",
        bundle_id=row.bundle_id or "",
        app_path=row.app_path or "",
        no_reset=bool(row.no_reset),
        full_reset=bool(row.full_reset),
        caps=row.caps_json if isinstance(row.caps_json, dict) else {},
        variables=_as_str_dict(row.variables_json),
        secret_keys=api_env_secrets.secret_keys(row.secrets_encrypted),
        accounts=_accounts(row.accounts_json if isinstance(row.accounts_json, list) else []),
        queue_name=row.queue_name or "app",
        max_concurrency=int(row.max_concurrency or 1),
        default_timeout_ms=int(row.default_timeout_ms or 15000),
        step_retries=int(row.step_retries if row.step_retries is not None else 1),
        is_default=bool(row.is_default),
        device_tags=[str(t) for t in row.device_tags_json] if isinstance(row.device_tags_json, list) else [],
        use_device_pool=bool(row.use_device_pool if row.use_device_pool is not None else True),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _clear_other_defaults(db: Session, project_id: int, keep_id: int | None = None) -> None:
    q = db.query(AppEnvironment).filter(
        AppEnvironment.project_id == project_id,
        AppEnvironment.is_default.is_(True),
    )
    if keep_id is not None:
        q = q.filter(AppEnvironment.id != keep_id)
    for row in q.all():
        row.is_default = False


def _try_encode_secrets(secrets: dict[str, str]) -> str | None:
    try:
        return api_env_secrets.encode_secrets(secrets)
    except ValueError as ex:
        raise AppError("bad_request", APP_ENV_SECRETS_ENCRYPT_FAILED, detail=str(ex), status_code=400) from ex


def _dump_accounts(items: list) -> list[dict]:
    out = []
    for it in items:
        if hasattr(it, "model_dump"):
            out.append(it.model_dump())
        elif isinstance(it, dict):
            out.append(it)
    return out


@router.get("/", response_model=Page[AppEnvOut])
def list_envs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[AppEnvOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(AppEnvironment).filter(AppEnvironment.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(AppEnvironment.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(AppEnvironment.updated_at.desc(), AppEnvironment.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[AppEnvOut](items=[_env_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=AppEnvOut, status_code=201)
def create_env(
    project_id: int,
    body: AppEnvCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppEnvOut:
    if body.is_default:
        _clear_other_defaults(db, project_id)
    row = AppEnvironment(
        project_id=project_id,
        name=body.name.strip(),
        platform=body.platform,
        appium_server_url=body.appium_server_url,
        device_name=body.device_name,
        udid=body.udid or "",
        platform_version=body.platform_version or "",
        automation_name=body.automation_name,
        app_package=body.app_package or "",
        app_activity=body.app_activity or "",
        bundle_id=body.bundle_id or "",
        app_path=body.app_path or "",
        no_reset=body.no_reset,
        full_reset=body.full_reset,
        caps_json=body.caps,
        variables_json=body.variables,
        secrets_encrypted=_try_encode_secrets(body.secrets),
        accounts_json=_dump_accounts(body.accounts),
        queue_name=body.queue_name or "app",
        max_concurrency=body.max_concurrency,
        default_timeout_ms=body.default_timeout_ms,
        step_retries=body.step_retries,
        is_default=body.is_default,
        device_tags_json=list(body.device_tags or []),
        use_device_pool=body.use_device_pool,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _env_out(row)


@router.get("/{env_id}", response_model=AppEnvOut)
def get_env(
    project_id: int,
    env_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> AppEnvOut:
    return _env_out(_require_env(db, project_id, env_id))


@router.patch("/{env_id}", response_model=AppEnvOut)
def update_env(
    project_id: int,
    env_id: int,
    body: AppEnvUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppEnvOut:
    row = _require_env(db, project_id, env_id)
    data = body.model_dump(exclude_unset=True)
    for field in (
        "platform",
        "appium_server_url",
        "device_name",
        "udid",
        "platform_version",
        "automation_name",
        "app_package",
        "app_activity",
        "bundle_id",
        "app_path",
        "no_reset",
        "full_reset",
        "queue_name",
        "max_concurrency",
        "default_timeout_ms",
        "step_retries",
    ):
        if field in data and data[field] is not None:
            setattr(row, field, data[field])
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "caps" in data:
        row.caps_json = data["caps"] or {}
    if "variables" in data:
        row.variables_json = data["variables"] or {}
    if "secrets" in data:
        row.secrets_encrypted = _try_encode_secrets(data["secrets"] or {})
    if "accounts" in data:
        row.accounts_json = _dump_accounts(body.accounts or [])
    if data.get("is_default") is True:
        _clear_other_defaults(db, project_id, keep_id=row.id)
        row.is_default = True
    elif data.get("is_default") is False:
        row.is_default = False
    if "device_tags" in data:
        row.device_tags_json = data["device_tags"] or []
    if "use_device_pool" in data and data["use_device_pool"] is not None:
        row.use_device_pool = data["use_device_pool"]
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
