from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase, ApiEnvironment, ApiDefinition, ApiRun
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.identity.user import User
from app.schemas.api_automation.api_automation import (
    ApiCaseBatchRunRequest,
    ApiCaseCreate,
    ApiCaseOut,
    ApiCaseRunRequest,
    ApiCaseUpdate,
    ApiDebugRequest,
    ApiDebugResponse,
    ApiRunOut,
)
from app.schemas.page import Page
from app.services.api_automation import api_env_secrets
from app.services.functional.cycle_attempt_sync import sync_attempt_quietly, validate_cycle_item
from app.services.api_automation.api_runner import execute_http_case
from app.services.api_automation.api_run_executor import execute_api_run
from app.tasks.api_automation.run_api_case import enqueue_run_api_case
from app.user_messages import (
    API_CASE_BATCH_EMPTY,
    API_CASE_BATCH_LIMIT,
    API_CASE_NOT_FOUND,
    API_DEFINITION_NOT_FOUND,
    API_ENV_BASE_URL_REQUIRED,
    API_ENV_NOT_FOUND,
    MANUAL_CASE_NOT_IN_PROJECT,
)

router = APIRouter(prefix="/projects/{project_id}/api-cases", tags=["api-cases"])


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_case(db: Session, project_id: int, case_id: int) -> ApiCase:
    row = db.query(ApiCase).filter(ApiCase.id == case_id, ApiCase.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", API_CASE_NOT_FOUND, status_code=404)
    return row


def _as_str_dict(data: dict | None) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _as_list(data: list | None) -> list:
    return data if isinstance(data, list) else []


def _validate_manual_case(db: Session, project_id: int, manual_case_id: int | None) -> None:
    if manual_case_id is None:
        return
    c = db.get(TestCase, manual_case_id)
    if c is None or c.project_id != project_id:
        raise AppError("bad_request", MANUAL_CASE_NOT_IN_PROJECT, status_code=400)


def _validate_api_definition(db: Session, project_id: int, api_id: int | None) -> None:
    if api_id is None:
        return
    row = db.query(ApiDefinition).filter(ApiDefinition.id == api_id, ApiDefinition.project_id == project_id).first()
    if row is None:
        raise AppError("bad_request", API_DEFINITION_NOT_FOUND, status_code=400)


def _dump_extractors(items: list) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if hasattr(it, "model_dump"):
            out.append(it.model_dump(by_alias=True))
        elif isinstance(it, dict):
            out.append(it)
    return out


def _case_out(row: ApiCase) -> ApiCaseOut:
    tags = row.tags_json if isinstance(row.tags_json, list) else None
    return ApiCaseOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        method=row.method,
        path=row.path,
        query=_as_str_dict(row.query_json),
        headers=_as_str_dict(row.headers_json),
        body_type=row.body_type or "none",
        body=row.body_text or "",
        extractors=_as_list(row.extractors_json),
        assertions=_as_list(row.assertions_json),
        timeout_ms=row.timeout_ms or 15000,
        manual_case_id=row.manual_case_id,
        api_id=row.api_id,
        status=row.status,
        tags_json=tags,
        creator_id=row.creator_id,
        pre_script=row.pre_script or "",
        post_script=row.post_script or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=Page[ApiCaseOut])
def list_cases(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
    api_id: int | None = Query(None),
) -> Page[ApiCaseOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(ApiCase).filter(ApiCase.project_id == project_id)
    if search and search.strip():
        filtered = filtered.filter(ApiCase.name.like(f"%{search.strip()}%"))
    if api_id is not None:
        filtered = filtered.filter(ApiCase.api_id == api_id)
    total = filtered.count()
    rows = filtered.order_by(ApiCase.updated_at.desc(), ApiCase.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[ApiCaseOut](items=[_case_out(r) for r in rows], total=total, page=p, page_size=ps)


@router.post("/", response_model=ApiCaseOut, status_code=201)
def create_case(
    project_id: int,
    body: ApiCaseCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiCaseOut:
    _validate_manual_case(db, project_id, body.manual_case_id)
    _validate_api_definition(db, project_id, body.api_id)
    row = ApiCase(
        project_id=project_id,
        api_id=body.api_id,
        name=body.name.strip(),
        description=body.description,
        method=body.method,
        path=body.path,
        query_json=body.query,
        headers_json=body.headers,
        body_type=body.body_type,
        body_text=body.body,
        extractors_json=_dump_extractors(body.extractors),
        assertions_json=[a.model_dump() for a in body.assertions],
        timeout_ms=body.timeout_ms,
        manual_case_id=body.manual_case_id,
        status=body.status,
        tags_json=body.tags,
        creator_id=user.id,
        pre_script=body.pre_script or "",
        post_script=body.post_script or "",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _case_out(row)


def _dedupe_case_ids(case_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for raw in case_ids:
        cid = int(raw)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    if not out:
        raise AppError("bad_request", API_CASE_BATCH_EMPTY, status_code=400)
    if len(out) > 50:
        raise AppError("bad_request", API_CASE_BATCH_LIMIT, status_code=400)
    return out


@router.post("/batch-run", response_model=ApiRunOut, status_code=201)
def batch_run_cases(
    project_id: int,
    body: ApiCaseBatchRunRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiRunOut:
    case_ids = _dedupe_case_ids(body.case_ids)
    found = {
        row.id
        for row in db.query(ApiCase)
        .filter(ApiCase.project_id == project_id, ApiCase.id.in_(case_ids))
        .all()
    }
    if any(cid not in found for cid in case_ids):
        raise AppError("not_found", API_CASE_NOT_FOUND, status_code=404)
    env = _resolve_env(db, project_id, body.env_id)
    if env is None or not (env.base_url or "").strip():
        raise AppError("bad_request", API_ENV_BASE_URL_REQUIRED, status_code=400)
    cycle_item = validate_cycle_item(db, project_id, body.cycle_item_id)
    run = ApiRun(
        project_id=project_id,
        trigger_type="batch",
        target_id=0,
        env_id=env.id,
        status="pending",
        summary_json={"case_ids": case_ids},
        cycle_item_id=cycle_item.id if cycle_item is not None else None,
        triggered_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if body.sync:
        execute_api_run(db, run.id)
        if run.cycle_item_id is not None:
            sync_attempt_quietly(db, run_type="api", run_id=run.id)
        db.refresh(run)
    else:
        task_id = enqueue_run_api_case(run.id)
        if task_id:
            run.celery_task_id = task_id
            db.commit()
            db.refresh(run)
    return ApiRunOut.model_validate(run)


@router.get("/{case_id}", response_model=ApiCaseOut)
def get_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ApiCaseOut:
    return _case_out(_require_case(db, project_id, case_id))


@router.patch("/{case_id}", response_model=ApiCaseOut)
def update_case(
    project_id: int,
    case_id: int,
    body: ApiCaseUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiCaseOut:
    row = _require_case(db, project_id, case_id)
    data = body.model_dump(exclude_unset=True, by_alias=True)
    if "manual_case_id" in data:
        _validate_manual_case(db, project_id, data["manual_case_id"])
    if "api_id" in data:
        _validate_api_definition(db, project_id, data["api_id"])
        row.api_id = data["api_id"]
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    if "method" in data and data["method"] is not None:
        row.method = data["method"]
    if "path" in data and data["path"] is not None:
        row.path = data["path"]
    if "query" in data:
        row.query_json = data["query"] or {}
    if "headers" in data:
        row.headers_json = data["headers"] or {}
    if "body_type" in data and data["body_type"] is not None:
        row.body_type = data["body_type"]
    if "body" in data and data["body"] is not None:
        row.body_text = data["body"]
    if "extractors" in data:
        raw = body.extractors or []
        row.extractors_json = _dump_extractors(raw)
    if "assertions" in data:
        raw_a = body.assertions or []
        row.assertions_json = [a.model_dump() for a in raw_a]
    if "timeout_ms" in data and data["timeout_ms"] is not None:
        row.timeout_ms = data["timeout_ms"]
    if "manual_case_id" in data:
        row.manual_case_id = data["manual_case_id"]
    if "status" in data and data["status"] is not None:
        row.status = data["status"]
    if "tags" in data:
        row.tags_json = data["tags"]
    if "pre_script" in data and data["pre_script"] is not None:
        row.pre_script = data["pre_script"]
    if "post_script" in data and data["post_script"] is not None:
        row.post_script = data["post_script"]
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


def _env_ctx_and_headers(env: ApiEnvironment | None, case_headers: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    ctx: dict[str, str] = {}
    headers: dict[str, str] = {}
    if env:
        if isinstance(env.variables_json, dict):
            ctx.update({str(k): str(v) for k, v in env.variables_json.items()})
        ctx.update(api_env_secrets.decode_secrets(env.secrets_encrypted))
        if isinstance(env.headers_json, dict):
            headers.update({str(k): str(v) for k, v in env.headers_json.items()})
    headers.update(case_headers or {})
    return ctx, headers


@router.post("/debug", response_model=ApiDebugResponse)
def debug_case(
    project_id: int,
    body: ApiDebugRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ApiDebugResponse:
    """同步调试一次请求（不落库、不走 Celery）。"""
    env = _resolve_env(db, project_id, body.env_id)
    if env is None or not (env.base_url or "").strip():
        raise AppError("bad_request", API_ENV_BASE_URL_REQUIRED, status_code=400)
    ctx, headers = _env_ctx_and_headers(env, body.headers)
    result = execute_http_case(
        method=body.method,
        base_url=env.base_url,
        path=body.path,
        query=body.query,
        headers=headers,
        body_type=body.body_type,
        body_text=body.body,
        timeout_ms=body.timeout_ms,
        assertions=[a.model_dump() for a in body.assertions],
        extractors=[e.model_dump(by_alias=True) for e in body.extractors],
        ctx=ctx,
        pre_script=body.pre_script,
        post_script=body.post_script,
    )
    return ApiDebugResponse(
        status=str(result.get("status") or "error"),
        error_message=str(result.get("error_message") or ""),
        request_snapshot=result.get("request_snapshot"),
        response_status=result.get("response_status"),
        response_headers=result.get("response_headers"),
        response_body=str(result.get("response_body") or ""),
        duration_ms=result.get("duration_ms"),
        assertion_results=list(result.get("assertion_results") or []),
        extract_results=dict(result.get("extract_results") or {}),
    )


@router.post("/{case_id}/run", response_model=ApiRunOut, status_code=201)
def run_case(
    project_id: int,
    case_id: int,
    body: ApiCaseRunRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ApiRunOut:
    case = _require_case(db, project_id, case_id)
    env = _resolve_env(db, project_id, body.env_id)
    cycle_item = validate_cycle_item(db, project_id, body.cycle_item_id)
    run = ApiRun(
        project_id=project_id,
        trigger_type="case",
        target_id=case.id,
        env_id=env.id if env else None,
        status="pending",
        cycle_item_id=cycle_item.id if cycle_item is not None else None,
        triggered_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if body.sync:
        execute_api_run(db, run.id)
        if run.cycle_item_id is not None:
            sync_attempt_quietly(db, run_type="api", run_id=run.id)
        db.refresh(run)
    else:
        task_id = enqueue_run_api_case(run.id)
        if task_id:
            run.celery_task_id = task_id
            db.commit()
            db.refresh(run)
    return ApiRunOut.model_validate(run)
