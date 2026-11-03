"""Helpers for API definition catalog (接口管理)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.api_automation.api_automation import ApiCase, ApiDefinition, ApiSpecEndpoint
from app.services.api_automation.openapi_import import endpoint_to_case_payload, normalize_path_template


def _as_str_dict(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def endpoint_sample_fields(ep: ApiSpecEndpoint | dict[str, Any]) -> dict[str, Any]:
    if isinstance(ep, ApiSpecEndpoint):
        sample = ep.request_sample_json if isinstance(ep.request_sample_json, dict) else {}
        tags = ep.tags_json if isinstance(ep.tags_json, list) else []
        method = ep.method
        path = ep.path
        summary = ep.summary
        operation_id = ep.operation_id
        tags_list = tags
        spec_id = ep.spec_id
        spec_endpoint_id = ep.id
    else:
        sample = ep.get("request_sample") if isinstance(ep.get("request_sample"), dict) else {}
        tags_list = ep.get("tags") if isinstance(ep.get("tags"), list) else []
        method = str(ep.get("method") or "GET")
        path = str(ep.get("path") or "/")
        summary = ep.get("summary")
        operation_id = ep.get("operation_id")
        spec_id = ep.get("spec_id")
        spec_endpoint_id = ep.get("spec_endpoint_id")

    path = normalize_path_template(str(path or "/"))
    body_type = sample.get("body_type") or "none"
    body_obj = sample.get("body")
    body = ""
    if body_type == "json" and body_obj is not None:
        body = json.dumps(body_obj, ensure_ascii=False, indent=2)
    elif body_type == "text" and body_obj is not None:
        body = str(body_obj)
    query = _as_str_dict(sample.get("query"))
    headers = {"Content-Type": "application/json"} if body_type == "json" else {}
    name = str(summary or operation_id or f"{method} {path}")
    return {
        "name": name,
        # OpenAPI tags stay in tags_json; do not map tag → project module on sync.
        "module": "",
        "description": str(summary or ""),
        "method": method.upper(),
        "path": path,
        "query_json": query,
        "headers_json": headers,
        "body_type": body_type,
        "body_text": body,
        "tags_json": tags_list,
        "operation_id": operation_id,
        "spec_id": spec_id,
        "spec_endpoint_id": spec_endpoint_id,
    }


def find_definition(db: Session, project_id: int, method: str, path: str) -> ApiDefinition | None:
    path_n = normalize_path_template(path)
    row = (
        db.query(ApiDefinition)
        .filter(
            ApiDefinition.project_id == project_id,
            ApiDefinition.method == method.upper(),
            ApiDefinition.path == path_n,
        )
        .first()
    )
    if row is not None:
        return row
    # Legacy OpenAPI `{name}` paths stored before runtime `{{name}}` normalization.
    if path_n != path:
        return (
            db.query(ApiDefinition)
            .filter(
                ApiDefinition.project_id == project_id,
                ApiDefinition.method == method.upper(),
                ApiDefinition.path == path,
            )
            .first()
        )
    # Incoming already normalized: scan same-method rows whose legacy path normalizes equal.
    candidates = (
        db.query(ApiDefinition)
        .filter(
            ApiDefinition.project_id == project_id,
            ApiDefinition.method == method.upper(),
        )
        .all()
    )
    for cand in candidates:
        if normalize_path_template(cand.path or "") == path_n:
            return cand
    return None


def upsert_definition_from_endpoint(
    db: Session,
    *,
    project_id: int,
    ep: ApiSpecEndpoint,
    creator_id: int | None,
) -> tuple[ApiDefinition, bool]:
    """Return (definition, created)."""
    fields = endpoint_sample_fields(ep)
    row = find_definition(db, project_id, fields["method"], fields["path"])
    if row is None:
        row = ApiDefinition(
            project_id=project_id,
            source="openapi",
            status="active",
            creator_id=creator_id,
            **{k: fields[k] for k in (
                "name",
                "module",
                "description",
                "method",
                "path",
                "query_json",
                "headers_json",
                "body_type",
                "body_text",
                "tags_json",
                "operation_id",
                "spec_id",
                "spec_endpoint_id",
            )},
        )
        db.add(row)
        db.flush()
        return row, True

    # refresh synced fields; keep existing module name (manual association).
    row.name = fields["name"]
    row.description = fields["description"]
    row.path = fields["path"]  # upgrade legacy `{id}` → `{{id}}`
    row.query_json = fields["query_json"]
    row.headers_json = fields["headers_json"]
    row.body_type = fields["body_type"]
    row.body_text = fields["body_text"]
    row.tags_json = fields["tags_json"]
    row.operation_id = fields["operation_id"]
    row.spec_id = fields["spec_id"]
    row.spec_endpoint_id = fields["spec_endpoint_id"]
    row.source = "openapi"
    row.status = "active"
    db.flush()
    return row, False


def definition_to_case(
    db: Session,
    *,
    project_id: int,
    definition: ApiDefinition,
    creator_id: int | None,
    status: str = "draft",
    name: str | None = None,
) -> ApiCase:
    payload = endpoint_to_case_payload(
        {
            "method": definition.method,
            "path": definition.path,
            "summary": name or definition.name,
            "tags": definition.tags_json,
            "request_sample": {
                "body_type": definition.body_type or "none",
                "body": (
                    json.loads(definition.body_text)
                    if (definition.body_type == "json" and (definition.body_text or "").strip())
                    else None
                ),
                "query": definition.query_json if isinstance(definition.query_json, dict) else {},
            },
        },
        status=status,
    )
    # prefer definition query/headers/body when present
    query = _as_str_dict(definition.query_json) or payload["query"]
    headers = _as_str_dict(definition.headers_json) or payload["headers"]
    body = definition.body_text if definition.body_text is not None else (payload["body"] or "")
    body_type = definition.body_type or payload["body_type"]
    if body_type == "json" and (definition.body_text or "").strip():
        try:
            json.loads(definition.body_text or "")
            body = definition.body_text or ""
        except json.JSONDecodeError:
            body = payload["body"] or definition.body_text or ""
    case = ApiCase(
        project_id=project_id,
        api_id=definition.id,
        name=payload["name"],
        method=payload["method"],
        path=payload["path"],
        query_json=query,
        headers_json=headers,
        body_type=body_type,
        body_text=body,
        assertions_json=payload["assertions"],
        extractors_json=payload["extractors"],
        timeout_ms=payload["timeout_ms"],
        status=payload["status"],
        tags_json=payload["tags"],
        creator_id=creator_id,
        description=definition.description or "",
    )
    db.add(case)
    db.flush()
    return case


def ensure_case_for_definition(
    db: Session,
    *,
    project_id: int,
    definition: ApiDefinition,
    creator_id: int | None,
) -> tuple[ApiCase, bool]:
    """Return an existing case bound to the definition, or create a draft one.

    Used by scenario orchestration so users pick interfaces (definitions)
    while execution still runs against concrete cases.
    """
    existing = (
        db.query(ApiCase)
        .filter(ApiCase.project_id == project_id, ApiCase.api_id == definition.id)
        .order_by(ApiCase.id.asc())
        .first()
    )
    if existing is not None:
        return existing, False
    case = definition_to_case(
        db,
        project_id=project_id,
        definition=definition,
        creator_id=creator_id,
        status="draft",
        name=None,
    )
    return case, True


def definition_to_step_payload(definition: ApiDefinition) -> dict[str, Any]:
    """Copy interface fields into an inline suite-step request payload."""
    query = _as_str_dict(definition.query_json)
    headers = _as_str_dict(definition.headers_json)
    body_type = definition.body_type or "none"
    body = definition.body_text or ""
    if body_type == "json" and body.strip():
        try:
            json.loads(body)
        except json.JSONDecodeError:
            body = body
    return {
        "definition_id": definition.id,
        "definition_name": definition.name,
        "name": definition.name,
        "method": (definition.method or "GET").upper(),
        "path": normalize_path_template(definition.path or "/"),
        "query": query,
        "headers": headers,
        "body_type": body_type,
        "body": body,
        "assertions": [{"type": "status", "expected": 200}],
        "extractors": [],
        "timeout_ms": 15000,
    }
