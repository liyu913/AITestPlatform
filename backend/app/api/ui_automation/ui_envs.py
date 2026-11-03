from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiEnvironment
from app.schemas.page import Page
from app.schemas.ui_automation.ui_automation import UiAccountOut, UiEnvCreate, UiEnvOut, UiEnvUpdate
from app.services.api_automation import api_env_secrets
from app.services.ui_automation.ui_env_auth import (
    account_auth_flags,
    normalize_token_inject,
    prune_account_storage_states,
)
from app.user_messages import UI_ENV_NOT_FOUND, UI_ENV_SECRETS_ENCRYPT_FAILED

router = APIRouter(prefix="/projects/{project_id}/ui-envs", tags=["ui-envs"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_env(db: Session, project_id: int, env_id: int) -> UiEnvironment:
    row = (
        db.query(UiEnvironment)
        .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
    return row


def _as_str_dict(data: dict | None) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _viewport(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {"width": 1280, "height": 720}
    return {"width": int(data.get("width") or 1280), "height": int(data.get("height") or 720)}


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


def _browsers(row: UiEnvironment) -> list[str]:
    if isinstance(row.browsers_json, list) and row.browsers_json:
        return [str(x) for x in row.browsers_json]
    return [row.browser or "chromium"]


def _env_out(row: UiEnvironment) -> UiEnvOut:
    accounts = account_auth_flags(
        row, _accounts(row.accounts_json if isinstance(row.accounts_json, list) else [])
    )
    return UiEnvOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        base_url=row.base_url,
        browser=row.browser or "chromium",
        browsers=_browsers(row),
        viewport=_viewport(row.viewport_json),
        variables=_as_str_dict(row.variables_json),
        secret_keys=api_env_secrets.secret_keys(row.secrets_encrypted),
        accounts=[UiAccountOut.model_validate(a) for a in accounts],
        token_inject=normalize_token_inject(row.token_inject),
        token_storage_key=(row.token_storage_key or None),
        inject_headers=_as_str_dict(row.inject_headers_json),
        inject_local_storage=_as_str_dict(row.inject_local_storage_json),
        queue_name=row.queue_name or "ui",
        max_concurrency=int(row.max_concurrency or 1),
        default_timeout_ms=int(row.default_timeout_ms or 15000),
        step_retries=int(row.step_retries if row.step_retries is not None else 1),
        is_default=bool(row.is_default),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _clear_other_defaults(db: Session, project_id: int, keep_id: int | None = None) -> None:
    q = db.query(UiEnvironment).filter(
        UiEnvironment.project_id == project_id,
        UiEnvironment.is_default.is_(True),
    )
    if keep_id is not None:
        q = q.filter(UiEnvironment.id != keep_id)
    for row in q.all():
        row.is_default = False


def _try_encode_secrets(secrets: dict[str, str]) -> str | None:
    try:
        return api_env_secrets.encode_secrets(secrets)
    except ValueError as ex:
        raise AppError("bad_request", UI_ENV_SECRETS_ENCRYPT_FAILED, detail=str(ex), status_code=400) from ex


def _dump_accounts(items: list) -> list[dict]:
    out = []
    for it in items:
        if hasattr(it, "model_dump"):
            out.append(it.model_dump())
        elif isinstance(it, dict):
            out.append(it)
    return out


@router.get("/", response_model=Page[UiEnvOut])
def list_envs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[UiEnvOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(UiEnvironment).filter(UiEnvironment.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(UiEnvironment.name.like(f"%{search.strip()}%"))
    total = filtered.count()
    rows = filtered.order_by(UiEnvironment.updated_at.desc(), UiEnvironment.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[UiEnvOut](items=[_env_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=UiEnvOut, status_code=201)
def create_env(
    project_id: int,
    body: UiEnvCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiEnvOut:
    if body.is_default:
        _clear_other_defaults(db, project_id)
    browsers = list(body.browsers) or [body.browser]
    row = UiEnvironment(
        project_id=project_id,
        name=body.name.strip(),
        base_url=body.base_url,
        browser=body.browser,
        browsers_json=browsers,
        viewport_json=body.viewport,
        variables_json=body.variables,
        secrets_encrypted=_try_encode_secrets(body.secrets),
        accounts_json=_dump_accounts(body.accounts),
        token_inject=normalize_token_inject(body.token_inject),
        token_storage_key=(body.token_storage_key or None),
        inject_headers_json=body.inject_headers or None,
        inject_local_storage_json=body.inject_local_storage or None,
        queue_name=body.queue_name or "ui",
        max_concurrency=body.max_concurrency,
        default_timeout_ms=body.default_timeout_ms,
        step_retries=body.step_retries,
        is_default=body.is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _env_out(row)


@router.get("/{env_id}", response_model=UiEnvOut)
def get_env(
    project_id: int,
    env_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> UiEnvOut:
    return _env_out(_require_env(db, project_id, env_id))


@router.patch("/{env_id}", response_model=UiEnvOut)
def update_env(
    project_id: int,
    env_id: int,
    body: UiEnvUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiEnvOut:
    row = _require_env(db, project_id, env_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "base_url" in data and data["base_url"] is not None:
        row.base_url = data["base_url"]
    if "browser" in data and data["browser"] is not None:
        row.browser = data["browser"]
    if "browsers" in data:
        row.browsers_json = data["browsers"] or [row.browser or "chromium"]
    if "viewport" in data:
        row.viewport_json = data["viewport"] or {"width": 1280, "height": 720}
    if "variables" in data:
        row.variables_json = data["variables"] or {}
    if "secrets" in data:
        row.secrets_encrypted = _try_encode_secrets(data["secrets"] or {})
    if "accounts" in data:
        dumped = _dump_accounts(body.accounts or [])
        row.accounts_json = dumped
        prune_account_storage_states(row, [str(a.get("name") or "") for a in dumped])
    if "token_inject" in data and data["token_inject"] is not None:
        row.token_inject = normalize_token_inject(data["token_inject"])
    if "token_storage_key" in data:
        row.token_storage_key = data["token_storage_key"] or None
    if "inject_headers" in data:
        row.inject_headers_json = data["inject_headers"] or None
    if "inject_local_storage" in data:
        row.inject_local_storage_json = data["inject_local_storage"] or None
    if "queue_name" in data and data["queue_name"] is not None:
        row.queue_name = data["queue_name"]
    if "max_concurrency" in data and data["max_concurrency"] is not None:
        row.max_concurrency = data["max_concurrency"]
    if "default_timeout_ms" in data and data["default_timeout_ms"] is not None:
        row.default_timeout_ms = data["default_timeout_ms"]
    if "step_retries" in data and data["step_retries"] is not None:
        row.step_retries = data["step_retries"]
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
