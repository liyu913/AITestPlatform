from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase, ApiDefinition, ApiSpec, ApiSpecEndpoint
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.api_automation.api_assets import (
    ApiDefinitionCreate,
    ApiDefinitionCreateCaseBody,
    ApiDefinitionGenerateCasesBody,
    ApiDefinitionOut,
    ApiDefinitionSyncFromSpecBody,
    ApiDefinitionUpdate,
)
from app.schemas.api_automation.api_automation import ApiCaseOut
from app.schemas.page import Page
from app.services.api_automation import api_case_gen as case_gen_svc
from app.services.api_automation import api_definitions as def_svc
from app.services.project import modules as module_svc
from app.user_messages import API_DEFINITION_NOT_FOUND, API_SPEC_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/api-definitions", tags=["api-definitions"])


def _as_str_dict(data: dict | None) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _require_def(db: Session, project_id: int, api_id: int) -> ApiDefinition:
    row = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.id == api_id, ApiDefinition.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", API_DEFINITION_NOT_FOUND, status_code=404)
    return row


def _case_count_map(db: Session, project_id: int, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = (
        db.query(ApiCase.api_id, func.count(ApiCase.id))
        .filter(ApiCase.project_id == project_id, ApiCase.api_id.in_(ids))
        .group_by(ApiCase.api_id)
        .all()
    )
    return {int(aid): int(cnt) for aid, cnt in rows if aid is not None}


def _def_out(row: ApiDefinition, case_count: int = 0) -> ApiDefinitionOut:
    return ApiDefinitionOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        module=row.module or "",
        module_id=row.module_id,
        description=row.description or "",
        method=row.method,
        path=row.path,
        query=_as_str_dict(row.query_json),
        headers=_as_str_dict(row.headers_json),
        body_type=row.body_type or "none",
        body=row.body_text or "",
        tags_json=row.tags_json if isinstance(row.tags_json, list) else None,
        status=row.status or "active",
        source=row.source or "manual",
        operation_id=row.operation_id,
        spec_id=row.spec_id,
        spec_endpoint_id=row.spec_endpoint_id,
        creator_id=row.creator_id,
        case_count=case_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_module_id(db: Session, project_id: int, row: ApiDefinition, module_id: int | None) -> None:
    if module_id is None:
        row.module_id = None
        row.module = ""
        return
    mod = module_svc.get_module(db, project_id, module_id)
    row.module_id = mod.id
    row.module = mod.name


@router.get("/", response_model=Page[ApiDefinitionOut])
def list_definitions(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    search: str | None = Query(None, max_length=256),
    module: str | None = Query(None, max_length=255),
    module_id: int | None = Query(None),
    ungrouped: bool = Query(False),
    method: str | None = Query(None, max_length=16),
    status: str | None = Query(None, max_length=32),
) -> Page[ApiDefinitionOut]:
    p, ps = max(1, page), min(500, max(1, page_size))
    q = db.query(ApiDefinition).filter(ApiDefinition.project_id == project_id)
    if search and search.strip():
        s = f"%{search.strip()}%"
        q = q.filter(
            (ApiDefinition.name.like(s))
            | (ApiDefinition.path.like(s))
            | (ApiDefinition.module.like(s))
        )
    if module_id is not None:
        q = q.filter(ApiDefinition.module_id == module_id)
    elif ungrouped:
        q = q.filter(ApiDefinition.module_id.is_(None))
    elif module is not None and module.strip() != "":
        q = q.filter(ApiDefinition.module == module.strip())
    if method:
        q = q.filter(ApiDefinition.method == method.strip().upper())
    if status:
        q = q.filter(ApiDefinition.status == status.strip())
    total = q.count()
    rows = q.order_by(ApiDefinition.module, ApiDefinition.path, ApiDefinition.id).offset((p - 1) * ps).limit(ps).all()
    counts = _case_count_map(db, project_id, [r.id for r in rows])
    return Page[ApiDefinitionOut](
        items=[_def_out(r, counts.get(r.id, 0)) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/modules")
def list_modules(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[str]:
    rows = (
        db.query(ApiDefinition.module)
        .filter(ApiDefinition.project_id == project_id, ApiDefinition.module != "")
        .distinct()
        .order_by(ApiDefinition.module)
        .all()
    )
    return [str(r[0]) for r in rows if r[0]]


@router.post("/", response_model=ApiDefinitionOut, status_code=201)
def create_definition(
    project_id: int,
    body: ApiDefinitionCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiDefinitionOut:
    existing = def_svc.find_definition(db, project_id, body.method, body.path)
    if existing is not None:
        raise AppError("conflict", f"接口已存在：{body.method} {body.path}", status_code=409)
    row = ApiDefinition(
        project_id=project_id,
        name=body.name.strip(),
        module=(body.module or "").strip(),
        description=body.description or "",
        method=body.method,
        path=body.path,
        query_json=body.query,
        headers_json=body.headers,
        body_type=body.body_type,
        body_text=body.body or "",
        tags_json=body.tags,
        status=body.status,
        source="manual",
        creator_id=user.id,
    )
    if body.module_id is not None:
        _apply_module_id(db, project_id, row, body.module_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _def_out(row, 0)


@router.post("/sync-from-spec")
def sync_from_spec(
    project_id: int,
    body: ApiDefinitionSyncFromSpecBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    spec = db.query(ApiSpec).filter(ApiSpec.id == body.spec_id, ApiSpec.project_id == project_id).first()
    if spec is None:
        raise AppError("not_found", API_SPEC_NOT_FOUND, status_code=404)
    q = db.query(ApiSpecEndpoint).filter(
        ApiSpecEndpoint.spec_id == body.spec_id,
        ApiSpecEndpoint.project_id == project_id,
    )
    if body.endpoint_ids:
        q = q.filter(ApiSpecEndpoint.id.in_(body.endpoint_ids))
    endpoints = q.all()
    created = updated = 0
    ids: list[int] = []
    for ep in endpoints:
        row, was_created = def_svc.upsert_definition_from_endpoint(
            db, project_id=project_id, ep=ep, creator_id=user.id
        )
        ids.append(row.id)
        if was_created:
            created += 1
        else:
            updated += 1
    db.commit()
    return {"created": created, "updated": updated, "ids": ids, "count": len(ids)}


@router.get("/{api_id}", response_model=ApiDefinitionOut)
def get_definition(
    project_id: int,
    api_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ApiDefinitionOut:
    row = _require_def(db, project_id, api_id)
    counts = _case_count_map(db, project_id, [row.id])
    return _def_out(row, counts.get(row.id, 0))


@router.patch("/{api_id}", response_model=ApiDefinitionOut)
def update_definition(
    project_id: int,
    api_id: int,
    body: ApiDefinitionUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiDefinitionOut:
    row = _require_def(db, project_id, api_id)
    data = body.model_dump(exclude_unset=True)
    if "method" in data or "path" in data:
        method = data.get("method", row.method)
        path = data.get("path", row.path)
        other = def_svc.find_definition(db, project_id, method, path)
        if other is not None and other.id != row.id:
            raise AppError("conflict", f"接口已存在：{method} {path}", status_code=409)
    for key, attr in (
        ("name", "name"),
        ("description", "description"),
        ("method", "method"),
        ("path", "path"),
        ("body_type", "body_type"),
        ("status", "status"),
    ):
        if key in data and data[key] is not None:
            setattr(row, attr, data[key] if key != "name" else str(data[key]).strip())
    if "module_id" in data:
        _apply_module_id(db, project_id, row, data["module_id"])
    elif "module" in data and data["module"] is not None:
        row.module = str(data["module"]).strip()
    if "query" in data and data["query"] is not None:
        row.query_json = data["query"]
    if "headers" in data and data["headers"] is not None:
        row.headers_json = data["headers"]
    if "body" in data and data["body"] is not None:
        row.body_text = data["body"]
    if "tags" in data:
        row.tags_json = data["tags"]
    if row.source != "openapi":
        row.source = "manual"
    db.commit()
    db.refresh(row)
    counts = _case_count_map(db, project_id, [row.id])
    return _def_out(row, counts.get(row.id, 0))


@router.delete("/{api_id}", status_code=204)
def delete_definition(
    project_id: int,
    api_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_def(db, project_id, api_id)
    db.delete(row)
    db.commit()


@router.post("/{api_id}/create-case", status_code=201)
def create_case_from_definition(
    project_id: int,
    api_id: int,
    body: ApiDefinitionCreateCaseBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    row = _require_def(db, project_id, api_id)
    case = def_svc.definition_to_case(
        db,
        project_id=project_id,
        definition=row,
        creator_id=user.id,
        status=body.status,
        name=body.name,
    )
    db.commit()
    return {"case_id": case.id, "api_id": row.id}


@router.post("/{api_id}/generate-cases", status_code=201)
def generate_cases_from_definition(
    project_id: int,
    api_id: int,
    body: ApiDefinitionGenerateCasesBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    """Generate multiple draft cases for one API using design rules (positive / negative / boundary)."""
    row = _require_def(db, project_id, api_id)
    cases = case_gen_svc.generate_cases_for_definition(
        db,
        project_id=project_id,
        definition=row,
        creator_id=user.id,
        status=body.status,
        max_cases=body.max_cases,
    )
    db.commit()
    return {
        "api_id": row.id,
        "count": len(cases),
        "case_ids": [c.id for c in cases],
        "names": [c.name for c in cases],
    }


@router.post("/{api_id}/ensure-case")
def ensure_case_from_definition(
    project_id: int,
    api_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    """Idempotent: reuse first case for this API, or create a draft case."""
    row = _require_def(db, project_id, api_id)
    case, created = def_svc.ensure_case_for_definition(
        db,
        project_id=project_id,
        definition=row,
        creator_id=user.id,
    )
    db.commit()
    return {
        "case_id": case.id,
        "api_id": row.id,
        "created": created,
        "case_name": case.name,
        "definition_name": row.name,
        "method": row.method,
        "path": row.path,
    }


@router.get("/{api_id}/cases", response_model=list[ApiCaseOut])
def list_cases_for_definition(
    project_id: int,
    api_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[ApiCaseOut]:
    from app.api.api_automation.api_cases import _case_out

    _require_def(db, project_id, api_id)
    rows = (
        db.query(ApiCase)
        .filter(ApiCase.project_id == project_id, ApiCase.api_id == api_id)
        .order_by(ApiCase.updated_at.desc(), ApiCase.id.desc())
        .all()
    )
    return [_case_out(r) for r in rows]
