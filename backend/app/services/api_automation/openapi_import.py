"""OpenAPI 3 / Swagger 2 document parsing into endpoint assets."""

from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any

from app.core.datetime_json import now_beijing

# OpenAPI `{name}` → runtime `{{name}}`; skip tokens already doubled.
_OPENAPI_PATH_PARAM_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def normalize_path_template(path: str) -> str:
    """Convert OpenAPI path placeholders `{name}` to runtime `{{name}}` (idempotent)."""
    text = (path or "").strip() or "/"
    return _OPENAPI_PATH_PARAM_RE.sub(r"{{\1}}", text)


def _load_doc(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("文档为空")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text)
        except Exception as ex:  # noqa: BLE001
            raise ValueError(f"无法解析接口文档（需 JSON 或 YAML）: {ex}") from ex
    if not isinstance(data, dict):
        raise ValueError("文档根节点必须是对象")
    return data


def _schema_store(doc: dict[str, Any]) -> dict[str, Any]:
    """OpenAPI3 components.schemas + Swagger2 definitions."""
    store: dict[str, Any] = {}
    defs = doc.get("definitions")
    if isinstance(defs, dict):
        store.update(defs)
    components = doc.get("components")
    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict):
            store.update(schemas)
    return store


def _resolve_schema(schema: dict[str, Any] | None, schemas: dict[str, Any], depth: int = 0) -> dict[str, Any] | None:
    if not schema or not isinstance(schema, dict):
        return None
    if "$ref" in schema and depth < 8:
        ref = str(schema["$ref"])
        name = ref.rsplit("/", 1)[-1]
        resolved = schemas.get(name)
        if isinstance(resolved, dict):
            return _resolve_schema(resolved, schemas, depth + 1)
        return None
    return schema


def _type_label(schema: dict[str, Any] | None, schemas: dict[str, Any]) -> str:
    if not schema:
        return "any"
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    resolved = _resolve_schema(schema, schemas) or schema
    t = resolved.get("type")
    if t == "array":
        items = resolved.get("items") if isinstance(resolved.get("items"), dict) else None
        return f"array<{_type_label(items, schemas)}>"
    if t:
        return str(t)
    if "properties" in resolved:
        return "object"
    if "allOf" in resolved:
        return "object"
    return "any"


def _rand_n() -> int:
    return random.randint(1000, 9999)


def _looks_like_datetime_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    lower = n.lower()
    if "datetime" in lower or lower.endswith("time"):
        return True
    if lower.endswith("_at"):
        return True
    return len(n) > 2 and n.endswith("At") and n[-3].islower()


def _looks_like_date_name(name: str) -> bool:
    lower = (name or "").strip().lower()
    return bool(lower) and lower.endswith("date") and "datetime" not in lower


def _looks_like_phone_name(name: str) -> bool:
    lower = (name or "").strip().lower()
    return "phone" in lower or "mobile" in lower


def _int_bounds(schema: dict[str, Any], *, default_lo: int = 1, default_hi: int = 9999) -> tuple[int, int]:
    lo, hi = default_lo, default_hi
    if "minimum" in schema:
        try:
            lo = int(schema["minimum"])
        except (TypeError, ValueError):
            pass
    if "maximum" in schema:
        try:
            hi = int(schema["maximum"])
        except (TypeError, ValueError):
            pass
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _sample_number(schema: dict[str, Any], *, integer: bool) -> int | float:
    if integer:
        lo, hi = _int_bounds(schema)
        return random.randint(lo, hi)
    lo_f, hi_f = 1.0, 9999.0
    if "minimum" in schema:
        try:
            lo_f = float(schema["minimum"])
        except (TypeError, ValueError):
            pass
    if "maximum" in schema:
        try:
            hi_f = float(schema["maximum"])
        except (TypeError, ValueError):
            pass
    if lo_f > hi_f:
        lo_f, hi_f = hi_f, lo_f
    return round(random.uniform(lo_f, hi_f), 2)


def _sample_string(schema: dict[str, Any], prop_name: str | None = None) -> str:
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return str(random.choice(enum))
    n = _rand_n()
    now = now_beijing().replace(microsecond=0)
    fmt = str(schema.get("format") or "").strip().lower()
    if fmt in ("date-time", "datetime"):
        return now.strftime("%Y-%m-%dT%H:%M:%S")
    if fmt == "date":
        return now.strftime("%Y-%m-%d")
    if fmt == "email":
        return f"user{n}@example.com"
    if fmt == "uuid":
        return str(uuid.uuid4())
    if fmt in ("uri", "url", "uri-reference"):
        return f"https://example.com/{n}"
    if not fmt and prop_name:
        if _looks_like_datetime_name(prop_name):
            return now.strftime("%Y-%m-%dT%H:%M:%S")
        if _looks_like_date_name(prop_name):
            return now.strftime("%Y-%m-%d")
        pl = prop_name.lower()
        if "email" in pl:
            return f"user{n}@example.com"
        if "uuid" in pl:
            return str(uuid.uuid4())
        if _looks_like_phone_name(prop_name):
            return "138" + "".join(str(random.randint(0, 9)) for _ in range(8))
    base = (prop_name or "example").strip() or "example"
    return f"{base}-{n}"


def _sample_from_schema(
    schema: dict[str, Any] | None,
    schemas: dict[str, Any],
    *,
    depth: int = 0,
    visiting: frozenset[str] | None = None,
    prop_name: str | None = None,
) -> Any:
    """Build a shallow example object; break cycles from circular $ref."""
    if not schema or not isinstance(schema, dict):
        return None
    if depth > 6:
        return None
    seen = visiting or frozenset()

    if "$ref" in schema:
        ref = str(schema["$ref"])
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return None
        target = schemas.get(name)
        if not isinstance(target, dict):
            return None
        return _sample_from_schema(
            target, schemas, depth=depth + 1, visiting=seen | {name}, prop_name=prop_name
        )

    t = schema.get("type")
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if t == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        if not isinstance(props, dict):
            return {}
        out: dict[str, Any] = {}
        for k, v in list(props.items())[:20]:
            if isinstance(v, dict):
                out[k] = _sample_from_schema(v, schemas, depth=depth + 1, visiting=seen, prop_name=k)
            else:
                out[k] = None
        return out
    if t == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else None
        item = _sample_from_schema(items, schemas, depth=depth + 1, visiting=seen)
        return [item] if item is not None else []
    if t == "integer":
        return _sample_number(schema, integer=True)
    if t == "number":
        return _sample_number(schema, integer=False)
    if t == "boolean":
        return True
    if t == "string" or (not t and (schema.get("format") or schema.get("enum"))):
        return _sample_string(schema, prop_name)
    # allOf / oneOf / anyOf: take first object-like branch
    for key in ("allOf", "oneOf", "anyOf"):
        parts = schema.get(key)
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    sample = _sample_from_schema(
                        part, schemas, depth=depth + 1, visiting=seen, prop_name=prop_name
                    )
                    if sample is not None:
                        return sample
    return _sample_string(schema, prop_name)


def _example_from_schema(
    schema: dict[str, Any] | None,
    schemas: dict[str, Any],
    *,
    prop_name: str | None = None,
) -> Any:
    if not schema:
        return None
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    resolved = _resolve_schema(schema, schemas)
    if resolved and resolved is not schema:
        if "example" in resolved:
            return resolved["example"]
        if "default" in resolved:
            return resolved["default"]
    return _sample_from_schema(schema, schemas, prop_name=prop_name)


def _fields_from_schema(
    schema: dict[str, Any] | None,
    schemas: dict[str, Any],
    *,
    prefix: str = "",
    depth: int = 0,
    required_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Flatten object properties into field rows (max depth 2)."""
    resolved = _resolve_schema(schema, schemas)
    if not resolved:
        return []
    props = resolved.get("properties")
    if not isinstance(props, dict):
        # non-object schema: single row
        if prefix:
            return []
        return [
            {
                "name": "(root)",
                "type": _type_label(resolved, schemas),
                "required": False,
                "description": str(resolved.get("description") or ""),
                "example": _example_from_schema(resolved, schemas),
            }
        ]

    req = set(resolved.get("required") or []) if isinstance(resolved.get("required"), list) else set()
    if required_names is not None and not prefix:
        req |= required_names

    rows: list[dict[str, Any]] = []
    for key, prop in list(props.items())[:40]:
        if not isinstance(prop, dict):
            continue
        name = f"{prefix}.{key}" if prefix else key
        prop_resolved = _resolve_schema(prop, schemas) or prop
        rows.append(
            {
                "name": name,
                "type": _type_label(prop, schemas),
                "required": key in req,
                "description": str(prop.get("description") or prop_resolved.get("description") or ""),
                "example": _example_from_schema(prop, schemas, prop_name=key),
            }
        )
        # nest one more level for objects
        if depth < 1 and (prop_resolved.get("type") == "object" or "properties" in prop_resolved):
            rows.extend(
                _fields_from_schema(
                    prop_resolved,
                    schemas,
                    prefix=name,
                    depth=depth + 1,
                )
            )
    return rows


def _base_url(doc: dict[str, Any]) -> str:
    servers = doc.get("servers") if isinstance(doc.get("servers"), list) else []
    if servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url") or "").strip()
        if url:
            return url
    host = str(doc.get("host") or "").strip()
    if not host:
        return ""
    schemes = doc.get("schemes") if isinstance(doc.get("schemes"), list) else []
    scheme = str(schemes[0]) if schemes else "https"
    base_path = str(doc.get("basePath") or "").rstrip("/")
    return f"{scheme}://{host}{base_path}"


def _json_schema_from_content(content: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    for key in ("application/json", "application/*+json", "*/*"):
        media = content.get(key)
        if isinstance(media, dict) and media.get("schema"):
            return media.get("schema") if isinstance(media.get("schema"), dict) else None
    for media in content.values():
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    return None


def _body_schema_from_op(op: dict[str, Any], path_item: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Return (schema, required)."""
    rb = op.get("requestBody")
    if isinstance(rb, dict):
        schema = _json_schema_from_content(rb.get("content") if isinstance(rb.get("content"), dict) else None)
        return schema, bool(rb.get("required"))
    # Swagger 2 body param
    for src in (path_item.get("parameters"), op.get("parameters")):
        if not isinstance(src, list):
            continue
        for p in src:
            if isinstance(p, dict) and str(p.get("in") or "") == "body":
                schema = p.get("schema") if isinstance(p.get("schema"), dict) else None
                return schema, bool(p.get("required"))
    return None, False


def _param_value(p: dict[str, Any], schemas: dict[str, Any]) -> str:
    if "example" in p:
        return str(p["example"])
    if "default" in p:
        return str(p["default"])
    name = str(p.get("name") or "")
    schema = p.get("schema") if isinstance(p.get("schema"), dict) else None
    if schema:
        ex = _example_from_schema(schema, schemas, prop_name=name)
        if ex is not None:
            return str(ex)
    mini: dict[str, Any] = {}
    for key in ("type", "format", "enum"):
        if key in p:
            mini[key] = p[key]
    if mini:
        ex = _sample_from_schema(mini, schemas, prop_name=name)
        if ex is not None:
            return str(ex)
    t = p.get("type") or (schema or {}).get("type") if schema else None
    if str(t or "") == "string" or not t:
        return _sample_string(mini or {"type": "string"}, name)
    return str(t)


def _collect_raw_params(op: dict[str, Any], path_item: dict[str, Any]) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for src in (path_item.get("parameters"), op.get("parameters")):
        if isinstance(src, list):
            for p in src:
                if isinstance(p, dict) and "$ref" not in p:
                    params.append(p)
    return params


def _params_docs(
    op: dict[str, Any],
    path_item: dict[str, Any],
    schemas: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[dict[str, Any]], Any]:
    """Return query, path_params, headers, parameter rows, swagger2_body sample."""
    query: dict[str, str] = {}
    path_params: dict[str, str] = {}
    headers: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    body_sample = None

    for p in _collect_raw_params(op, path_item):
        loc = str(p.get("in") or "")
        name = str(p.get("name") or "")
        schema = p.get("schema") if isinstance(p.get("schema"), dict) else None
        if loc == "body":
            body_sample = _sample_from_schema(schema or p.get("schema"), schemas)
            continue
        if not name:
            continue
        example = _param_value(p, schemas)
        if loc == "query":
            query[name] = example
        elif loc == "path":
            path_params[name] = example
        elif loc == "header":
            headers[name] = example
        else:
            continue
        typ = _type_label(schema, schemas) if schema else str(p.get("type") or "string")
        rows.append(
            {
                "name": name,
                "in": loc,
                "type": typ,
                "required": bool(p.get("required")) or loc == "path",
                "description": str(p.get("description") or ""),
                "example": example,
            }
        )
    return query, path_params, headers, rows, body_sample


def _responses_docs(op: dict[str, Any], schemas: dict[str, Any]) -> list[dict[str, Any]]:
    responses = op.get("responses")
    if not isinstance(responses, dict):
        return []

    def sort_key(status: str) -> tuple[int, str]:
        if status in ("200", "201"):
            return (0, status)
        if status == "default":
            return (2, status)
        return (1, status)

    out: list[dict[str, Any]] = []
    for status in sorted(responses.keys(), key=sort_key)[:5]:
        resp = responses[status]
        if not isinstance(resp, dict):
            continue
        schema = _json_schema_from_content(resp.get("content") if isinstance(resp.get("content"), dict) else None)
        if schema is None and isinstance(resp.get("schema"), dict):
            schema = resp["schema"]  # swagger2
        fields = _fields_from_schema(schema, schemas) if schema else []
        sample = _sample_from_schema(schema, schemas) if schema else None
        out.append(
            {
                "status": str(status),
                "description": str(resp.get("description") or ""),
                "fields": fields,
                "sample": sample,
            }
        )
    return out


def parse_openapi(raw: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (summary, endpoints[]). Supports OpenAPI 3.x and Swagger 2.0."""
    doc = _load_doc(raw)
    is_swagger2 = str(doc.get("swagger") or "").startswith("2")
    is_openapi3 = str(doc.get("openapi") or "").startswith("3")
    if not is_swagger2 and not is_openapi3 and "paths" not in doc:
        raise ValueError("需提供 OpenAPI 3.x 或 Swagger 2.0 文档（含 paths）")

    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    paths = doc.get("paths") if isinstance(doc.get("paths"), dict) else {}
    schemas = _schema_store(doc)
    base = _base_url(doc)

    endpoints: list[dict[str, Any]] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            m = str(method).upper()
            if m not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            if not isinstance(op, dict):
                continue

            query, path_params, headers, param_rows, sw_body = _params_docs(op, item, schemas)
            body_schema, _body_required = _body_schema_from_op(op, item)
            body_sample = _sample_from_schema(body_schema, schemas) if body_schema else sw_body
            request_fields = _fields_from_schema(body_schema, schemas) if body_schema else []
            responses = _responses_docs(op, schemas)

            path_tpl = normalize_path_template(str(path))
            endpoints.append(
                {
                    "method": m,
                    "path": path_tpl,
                    "operation_id": op.get("operationId"),
                    "summary": op.get("summary") or op.get("operationId") or f"{m} {path_tpl}",
                    "description": op.get("description") or "",
                    "tags": op.get("tags") if isinstance(op.get("tags"), list) else [],
                    "request_sample": {
                        "body_type": "json" if body_sample is not None else "none",
                        "body": body_sample,
                        "query": query,
                        "path_params": path_params,
                        "headers": headers,
                        "parameters": param_rows,
                        "request_fields": request_fields,
                        "responses": responses,
                    },
                }
            )

    source = "swagger2" if is_swagger2 else ("openapi3" if is_openapi3 else "openapi")
    summary = {
        "title": info.get("title") or ("Swagger" if is_swagger2 else "OpenAPI"),
        "version": info.get("version"),
        "base_url": base,
        "endpoint_count": len(endpoints),
        "source": source,
    }
    return summary, endpoints


def endpoint_to_case_payload(ep: dict[str, Any], *, status: str = "draft") -> dict[str, Any]:
    sample = ep.get("request_sample") if isinstance(ep.get("request_sample"), dict) else {}
    body_type = sample.get("body_type") or "none"
    body_obj = sample.get("body")
    query = sample.get("query") if isinstance(sample.get("query"), dict) else {}
    body = ""
    if body_type == "json" and body_obj is not None:
        body = json.dumps(body_obj, ensure_ascii=False, indent=2)
    path = normalize_path_template(str(ep.get("path") or "/"))
    return {
        "name": str(ep.get("summary") or f"{ep.get('method')} {path}"),
        "method": ep.get("method") or "GET",
        "path": path,
        "query": {str(k): str(v) for k, v in query.items()},
        "headers": {"Content-Type": "application/json"} if body_type == "json" else {},
        "body_type": body_type,
        "body": body,
        "assertions": [{"type": "status", "expected": 200}],
        "extractors": [],
        "timeout_ms": 15000,
        "status": status,
        "tags": ep.get("tags") if isinstance(ep.get("tags"), list) else [],
    }
