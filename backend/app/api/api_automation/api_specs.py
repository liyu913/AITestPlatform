from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiSpec, ApiSpecEndpoint
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.api_automation.api_assets import (
    ApiSpecEndpointOut,
    ApiSpecImportBody,
    ApiSpecImportToCasesBody,
    ApiSpecOut,
)
from app.schemas.page import Page
from app.services.api_automation import api_definitions as def_svc
from app.services.api_automation.openapi_import import normalize_path_template, parse_openapi
from app.user_messages import API_SPEC_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/api-specs", tags=["api-specs"])


def _spec_out(row: ApiSpec) -> ApiSpecOut:
    return ApiSpecOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        source_type=row.source_type,
        summary_json=row.summary_json if isinstance(row.summary_json, dict) else None,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ep_out(row: ApiSpecEndpoint) -> ApiSpecEndpointOut:
    return ApiSpecEndpointOut(
        id=row.id,
        spec_id=row.spec_id,
        project_id=row.project_id,
        method=row.method,
        path=row.path,
        operation_id=row.operation_id,
        summary=row.summary,
        tags_json=row.tags_json if isinstance(row.tags_json, list) else None,
        request_sample_json=row.request_sample_json if isinstance(row.request_sample_json, dict) else None,
    )


def _require_spec(db: Session, project_id: int, spec_id: int) -> ApiSpec:
    spec = db.query(ApiSpec).filter(ApiSpec.id == spec_id, ApiSpec.project_id == project_id).first()
    if spec is None:
        raise AppError("not_found", API_SPEC_NOT_FOUND, status_code=404)
    return spec


def _filter_endpoints_by_tag(q, tag: str, db: Session):
    """Match tag inside tags_json list; MySQL uses JSON_CONTAINS, else string LIKE."""
    t = tag.strip()
    if not t:
        return q
    bind = db.get_bind()
    if bind.dialect.name == "mysql":
        return q.filter(func.json_contains(ApiSpecEndpoint.tags_json, func.json_quote(t)))
    # SQLite / others: JSON stored as text
    return q.filter(cast(ApiSpecEndpoint.tags_json, String).like(f'%"{t}"%'))


@router.get("/", response_model=Page[ApiSpecOut])
def list_specs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[ApiSpecOut]:
    p, ps = max(1, page), min(100, max(1, page_size))
    q = db.query(ApiSpec).filter(ApiSpec.project_id == project_id)
    if search and search.strip():
        q = q.filter(ApiSpec.name.like(f"%{search.strip()}%"))
    total = q.count()
    rows = q.order_by(ApiSpec.updated_at.desc(), ApiSpec.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[ApiSpecOut](items=[_spec_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/import", response_model=ApiSpecOut, status_code=201)
def import_spec(
    project_id: int,
    body: ApiSpecImportBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiSpecOut:
    try:
        summary, endpoints = parse_openapi(body.content)
    except ValueError as ex:
        raise AppError("bad_request", str(ex), status_code=400) from ex
    except RecursionError as ex:
        raise AppError(
            "bad_request",
            "文档 schema 存在循环引用，解析失败；请简化 components/schemas 后重试",
            status_code=400,
        ) from ex
    source = "swagger2" if (summary or {}).get("source") == "swagger2" else "openapi"
    spec = ApiSpec(
        project_id=project_id,
        name=body.name.strip(),
        source_type=source,
        raw_text=body.content,
        summary_json=summary,
        creator_id=user.id,
    )
    db.add(spec)
    db.flush()
    for ep in endpoints:
        db.add(
            ApiSpecEndpoint(
                spec_id=spec.id,
                project_id=project_id,
                method=ep["method"],
                path=ep["path"],
                operation_id=ep.get("operation_id"),
                summary=ep.get("summary"),
                tags_json=ep.get("tags"),
                request_sample_json=ep.get("request_sample"),
            )
        )
    try:
        db.commit()
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        msg = str(ex)
        if "Data too long" in msg or "1406" in msg:
            raise AppError(
                "bad_request",
                "文档内容过大，无法写入数据库（请确认 api_specs.raw_text 已升级为 MEDIUMTEXT）",
                status_code=400,
            ) from ex
        raise
    db.refresh(spec)
    return _spec_out(spec)


@router.get("/{spec_id}/endpoint-tags", response_model=list[str])
def list_endpoint_tags(
    project_id: int,
    spec_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[str]:
    _require_spec(db, project_id, spec_id)
    rows = (
        db.query(ApiSpecEndpoint.tags_json)
        .filter(ApiSpecEndpoint.spec_id == spec_id, ApiSpecEndpoint.project_id == project_id)
        .all()
    )
    tags: set[str] = set()
    for (raw,) in rows:
        if isinstance(raw, list):
            for t in raw:
                s = str(t).strip()
                if s:
                    tags.add(s)
    return sorted(tags)


@router.get("/{spec_id}/endpoints", response_model=Page[ApiSpecEndpointOut])
def list_endpoints(
    project_id: int,
    spec_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
    method: str | None = Query(None, max_length=16),
    tag: str | None = Query(None, max_length=128),
) -> Page[ApiSpecEndpointOut]:
    _require_spec(db, project_id, spec_id)
    p, ps = max(1, page), min(100, max(1, page_size))
    q = db.query(ApiSpecEndpoint).filter(
        ApiSpecEndpoint.spec_id == spec_id,
        ApiSpecEndpoint.project_id == project_id,
    )
    if search and search.strip():
        s = f"%{search.strip()}%"
        q = q.filter(
            or_(
                ApiSpecEndpoint.path.like(s),
                ApiSpecEndpoint.summary.like(s),
                ApiSpecEndpoint.operation_id.like(s),
            )
        )
    if method and method.strip():
        q = q.filter(ApiSpecEndpoint.method == method.strip().upper())
    if tag and tag.strip():
        q = _filter_endpoints_by_tag(q, tag, db)
    total = q.count()
    rows = (
        q.order_by(ApiSpecEndpoint.method, ApiSpecEndpoint.path, ApiSpecEndpoint.id)
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )
    return Page[ApiSpecEndpointOut](
        items=[_ep_out(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/{spec_id}/reparse", response_model=ApiSpecOut)
def reparse_spec(
    project_id: int,
    spec_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiSpecOut:
    """Re-parse stored raw_text and refresh endpoint detail fields (no re-upload)."""
    spec = _require_spec(db, project_id, spec_id)
    raw = (spec.raw_text or "").strip()
    if not raw:
        raise AppError("bad_request", "文档原文为空，请重新导入 OpenAPI/Swagger", status_code=400)
    try:
        summary, endpoints = parse_openapi(raw)
    except ValueError as ex:
        raise AppError("bad_request", str(ex), status_code=400) from ex
    except RecursionError as ex:
        raise AppError(
            "bad_request",
            "文档 schema 存在循环引用，解析失败；请简化 components/schemas 后重试",
            status_code=400,
        ) from ex

    source = "swagger2" if (summary or {}).get("source") == "swagger2" else "openapi"
    spec.summary_json = summary
    spec.source_type = source

    existing = (
        db.query(ApiSpecEndpoint)
        .filter(ApiSpecEndpoint.spec_id == spec_id, ApiSpecEndpoint.project_id == project_id)
        .all()
    )
    # Key by normalized path so legacy `{id}` rows match new `{{id}}` endpoints.
    by_key: dict[tuple[str, str], ApiSpecEndpoint] = {}
    for r in existing:
        by_key[(r.method.upper(), normalize_path_template(r.path or "/"))] = r
    seen: set[tuple[str, str]] = set()
    for ep in endpoints:
        path_n = normalize_path_template(str(ep["path"] or "/"))
        key = (str(ep["method"]).upper(), path_n)
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            db.add(
                ApiSpecEndpoint(
                    spec_id=spec.id,
                    project_id=project_id,
                    method=ep["method"],
                    path=path_n,
                    operation_id=ep.get("operation_id"),
                    summary=ep.get("summary"),
                    tags_json=ep.get("tags"),
                    request_sample_json=ep.get("request_sample"),
                )
            )
        else:
            row.path = path_n
            row.operation_id = ep.get("operation_id")
            row.summary = ep.get("summary")
            row.tags_json = ep.get("tags")
            row.request_sample_json = ep.get("request_sample")
    for key, row in by_key.items():
        if key not in seen:
            db.delete(row)
    db.commit()
    db.refresh(spec)
    return _spec_out(spec)


@router.post("/{spec_id}/sync-definitions", status_code=201)
def sync_definitions(
    project_id: int,
    spec_id: int,
    body: ApiSpecImportToCasesBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    """Sync selected endpoints into 接口管理 (upsert by method+path)."""
    _require_spec(db, project_id, spec_id)
    rows = (
        db.query(ApiSpecEndpoint)
        .filter(
            ApiSpecEndpoint.spec_id == spec_id,
            ApiSpecEndpoint.project_id == project_id,
            ApiSpecEndpoint.id.in_(body.endpoint_ids),
        )
        .all()
    )
    created = updated = 0
    ids: list[int] = []
    for ep in rows:
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


@router.post("/{spec_id}/import-cases", status_code=201)
def import_endpoints_as_cases(
    project_id: int,
    spec_id: int,
    body: ApiSpecImportToCasesBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    _require_spec(db, project_id, spec_id)
    rows = (
        db.query(ApiSpecEndpoint)
        .filter(
            ApiSpecEndpoint.spec_id == spec_id,
            ApiSpecEndpoint.project_id == project_id,
            ApiSpecEndpoint.id.in_(body.endpoint_ids),
        )
        .all()
    )
    created_ids: list[int] = []
    for ep in rows:
        definition, _ = def_svc.upsert_definition_from_endpoint(
            db, project_id=project_id, ep=ep, creator_id=user.id
        )
        case = def_svc.definition_to_case(
            db,
            project_id=project_id,
            definition=definition,
            creator_id=user.id,
            status=body.status,
        )
        created_ids.append(case.id)
    db.commit()
    return {"created_ids": created_ids, "count": len(created_ids)}


@router.delete("/{spec_id}", status_code=204)
def delete_spec(
    project_id: int,
    spec_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    spec = _require_spec(db, project_id, spec_id)
    db.delete(spec)
    db.commit()
