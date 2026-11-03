"""Rule-based multi-case generation for a single API definition.

Design coverage (接口用例设计规则):
- 正向：完整样例 / 成功响应断言
- 异常：空 Body、非法 JSON、缺字段、类型错误
- 边界：空字符串、超长字符串（对字符串字段）
"""

from __future__ import annotations

import copy
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.api_automation.api_automation import ApiCase, ApiDefinition, ApiSpecEndpoint


def _as_str_dict(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _load_body(definition: ApiDefinition) -> Any:
    text = (definition.body_text or "").strip()
    if not text:
        return None
    if (definition.body_type or "none") == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _sample_context(db: Session, definition: ApiDefinition) -> dict[str, Any]:
    """Merge definition fields with linked OpenAPI endpoint sample when present."""
    sample: dict[str, Any] = {
        "body_type": definition.body_type or "none",
        "body": _load_body(definition),
        "query": _as_str_dict(definition.query_json),
        "headers": _as_str_dict(definition.headers_json),
        "parameters": [],
        "request_fields": [],
        "responses": [],
        "path_params": {},
    }
    if definition.spec_endpoint_id:
        ep = (
            db.query(ApiSpecEndpoint)
            .filter(ApiSpecEndpoint.id == definition.spec_endpoint_id)
            .first()
        )
        if ep and isinstance(ep.request_sample_json, dict):
            rs = ep.request_sample_json
            for k in (
                "body_type",
                "body",
                "query",
                "headers",
                "parameters",
                "request_fields",
                "responses",
                "path_params",
            ):
                if k in rs and rs[k] is not None:
                    sample[k] = rs[k]
    # prefer live definition overrides when set
    if definition.body_text is not None and (definition.body_text or "").strip() != "":
        sample["body"] = _load_body(definition)
        sample["body_type"] = definition.body_type or sample.get("body_type") or "none"
    if isinstance(definition.query_json, dict) and definition.query_json:
        sample["query"] = _as_str_dict(definition.query_json)
    if isinstance(definition.headers_json, dict) and definition.headers_json:
        sample["headers"] = _as_str_dict(definition.headers_json)
    return sample


def _success_assertions(sample: dict[str, Any]) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = [{"type": "status", "expected": 200}]
    responses = sample.get("responses") if isinstance(sample.get("responses"), list) else []
    ok = None
    for r in responses:
        if not isinstance(r, dict):
            continue
        st = str(r.get("status") or "")
        if st.startswith("2"):
            ok = r
            break
    if ok and isinstance(ok.get("sample"), dict):
        sm = ok["sample"]
        if "code" in sm and isinstance(sm["code"], (int, float)):
            assertions.append({"type": "jsonpath", "expr": "$.code", "op": "eq", "expected": int(sm["code"])})
        if "success" in sm and isinstance(sm["success"], bool):
            assertions.append(
                {"type": "jsonpath", "expr": "$.success", "op": "eq", "expected": sm["success"]}
            )
    return assertions


def _client_error_assertions(expected: int = 400) -> list[dict[str, Any]]:
    return [{"type": "status", "expected": expected}]


def _dump_body(body: Any, body_type: str) -> str:
    if body_type == "json":
        if body is None:
            return ""
        if isinstance(body, (dict, list)):
            return json.dumps(body, ensure_ascii=False, indent=2)
        return str(body)
    if body is None:
        return ""
    return str(body)


def _pick_string_fields(fields: list[dict[str, Any]], body: Any) -> list[str]:
    names: list[str] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "")
        if not name or "." in name:
            continue
        t = str(f.get("type") or "").lower()
        if t in ("string", "str", "") or not t:
            names.append(name)
    if isinstance(body, dict):
        for k, v in body.items():
            if isinstance(v, str) and k not in names:
                names.append(str(k))
    # prefer business-looking keys
    preferred = ["name", "title", "username", "email", "phone", "contactPhone", "contactName", "address"]
    names.sort(key=lambda n: (0 if n in preferred else 1, preferred.index(n) if n in preferred else 99))
    return names


def _pick_number_fields(fields: list[dict[str, Any]], body: Any) -> list[str]:
    names: list[str] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "")
        if not name or "." in name:
            continue
        t = str(f.get("type") or "").lower()
        if t in ("integer", "int", "number", "float", "long"):
            names.append(name)
    if isinstance(body, dict):
        for k, v in body.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in names:
                names.append(str(k))
    return names


def _required_fields(fields: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for f in fields:
        if isinstance(f, dict) and f.get("required") and f.get("name") and "." not in str(f["name"]):
            out.append(str(f["name"]))
    return out


def _omit_body_key(body: Any, key: str) -> Any:
    if not isinstance(body, dict):
        return body
    cloned = copy.deepcopy(body)
    cloned.pop(key, None)
    return cloned


def _set_body_key(body: Any, key: str, value: Any) -> Any:
    if not isinstance(body, dict):
        return {key: value}
    cloned = copy.deepcopy(body)
    cloned[key] = value
    return cloned


def build_case_blueprints(definition: ApiDefinition, sample: dict[str, Any]) -> list[dict[str, Any]]:
    """Return case payloads (not yet persisted), ordered by design coverage."""
    method = (definition.method or "GET").upper()
    path = definition.path or "/"
    base_name = definition.name or f"{method} {path}"
    body_type = str(sample.get("body_type") or definition.body_type or "none")
    body = sample.get("body")
    query = _as_str_dict(sample.get("query"))
    headers = _as_str_dict(sample.get("headers"))
    fields = sample.get("request_fields") if isinstance(sample.get("request_fields"), list) else []
    params = sample.get("parameters") if isinstance(sample.get("parameters"), list) else []

    has_json_body = body_type == "json" and method in {"POST", "PUT", "PATCH"}
    blueprints: list[dict[str, Any]] = []

    def add(
        *,
        suffix: str,
        tags: list[str],
        assertions: list[dict[str, Any]],
        body_val: Any = body,
        body_type_val: str = body_type,
        query_val: dict[str, str] | None = None,
        headers_val: dict[str, str] | None = None,
        path_val: str | None = None,
        raw_body: str | None = None,
    ) -> None:
        bt = body_type_val
        if raw_body is not None:
            body_text = raw_body
        else:
            body_text = _dump_body(body_val, bt) if bt != "none" else ""
            if bt != "none" and body_val is None and raw_body is None:
                body_text = ""
        hdrs = dict(headers_val if headers_val is not None else headers)
        if bt == "json" and "Content-Type" not in hdrs:
            hdrs["Content-Type"] = "application/json"
        blueprints.append(
            {
                "name": f"{base_name} - {suffix}",
                "method": method,
                "path": path_val or path,
                "query": dict(query_val if query_val is not None else query),
                "headers": hdrs,
                "body_type": bt if (raw_body is not None or body_val is not None or bt == "json") else "none",
                "body": body_text,
                "assertions": assertions,
                "extractors": [],
                "timeout_ms": 15000,
                "status": "draft",
                "tags": tags,
                "description": f"规则生成：{suffix}",
            }
        )

    # 1) 正向正常
    add(
        suffix="正向-正常请求",
        tags=["auto-gen", "positive"],
        assertions=_success_assertions(sample),
    )

    # 2) 缺必填（OpenAPI required）
    req_fields = _required_fields(fields)
    for key in req_fields[:3]:
        if has_json_body and isinstance(body, dict) and key in body:
            add(
                suffix=f"异常-缺少必填字段-{key}",
                tags=["auto-gen", "negative", "required"],
                assertions=_client_error_assertions(400),
                body_val=_omit_body_key(body, key),
            )

    # required query params from parameters list
    for p in params:
        if not isinstance(p, dict):
            continue
        if str(p.get("in") or "") != "query":
            continue
        if not p.get("required"):
            continue
        pname = str(p.get("name") or "")
        if not pname:
            continue
        q2 = dict(query)
        q2.pop(pname, None)
        add(
            suffix=f"异常-缺少必填Query-{pname}",
            tags=["auto-gen", "negative", "required"],
            assertions=_client_error_assertions(400),
            query_val=q2,
        )
        if len([b for b in blueprints if "缺少必填Query" in b["name"]]) >= 2:
            break

    # 3) 启发式：无 required 时，对写接口去掉首个业务字符串字段
    if has_json_body and isinstance(body, dict) and not req_fields:
        for key in _pick_string_fields(fields, body)[:1]:
            if key in body:
                add(
                    suffix=f"异常-缺少字段-{key}",
                    tags=["auto-gen", "negative"],
                    assertions=_client_error_assertions(400),
                    body_val=_omit_body_key(body, key),
                )

    # 4) 空 Body
    if has_json_body:
        add(
            suffix="异常-空Body",
            tags=["auto-gen", "negative", "empty-body"],
            assertions=_client_error_assertions(400),
            body_type_val="json",
            raw_body="",
        )
        add(
            suffix="异常-非法JSON",
            tags=["auto-gen", "negative", "invalid-json"],
            assertions=_client_error_assertions(400),
            body_type_val="json",
            raw_body="{",
        )

    # 5) 类型错误
    if has_json_body and isinstance(body, dict):
        for key in _pick_number_fields(fields, body)[:1]:
            add(
                suffix=f"异常-类型错误-{key}",
                tags=["auto-gen", "negative", "type"],
                assertions=_client_error_assertions(400),
                body_val=_set_body_key(body, key, "not-a-number"),
            )

    # 6) 边界：空串 / 超长
    if has_json_body and isinstance(body, dict):
        for key in _pick_string_fields(fields, body)[:1]:
            add(
                suffix=f"边界-空字符串-{key}",
                tags=["auto-gen", "boundary"],
                assertions=_client_error_assertions(400),
                body_val=_set_body_key(body, key, ""),
            )
            add(
                suffix=f"边界-超长字符串-{key}",
                tags=["auto-gen", "boundary"],
                assertions=_client_error_assertions(400),
                body_val=_set_body_key(body, key, "测" * 500),
            )

    # 7) GET：无 Query 时仍保留正向；有 query 则生成缺参/空参
    if method == "GET" and query:
        add(
            suffix="边界-清空全部Query",
            tags=["auto-gen", "boundary"],
            assertions=_success_assertions(sample),
            query_val={},
        )

    # 8) 文档声明 401 时：无鉴权头
    responses = sample.get("responses") if isinstance(sample.get("responses"), list) else []
    if any(isinstance(r, dict) and str(r.get("status")) == "401" for r in responses):
        hdrs = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "token", "x-token"}}
        add(
            suffix="异常-未授权",
            tags=["auto-gen", "negative", "auth"],
            assertions=_client_error_assertions(401),
            headers_val=hdrs,
        )

    # de-dupe by name
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for bp in blueprints:
        if bp["name"] in seen:
            continue
        seen.add(bp["name"])
        unique.append(bp)
    return unique


def generate_cases_for_definition(
    db: Session,
    *,
    project_id: int,
    definition: ApiDefinition,
    creator_id: int | None,
    status: str = "draft",
    max_cases: int = 12,
) -> list[ApiCase]:
    sample = _sample_context(db, definition)
    blueprints = build_case_blueprints(definition, sample)[: max(1, min(max_cases, 20))]
    created: list[ApiCase] = []
    for bp in blueprints:
        case = ApiCase(
            project_id=project_id,
            api_id=definition.id,
            name=bp["name"],
            description=bp.get("description") or "",
            method=bp["method"],
            path=bp["path"],
            query_json=bp["query"],
            headers_json=bp["headers"],
            body_type=bp["body_type"],
            body_text=bp["body"],
            assertions_json=bp["assertions"],
            extractors_json=bp["extractors"],
            timeout_ms=bp["timeout_ms"],
            status=status or bp["status"],
            tags_json=bp["tags"],
            creator_id=creator_id,
        )
        db.add(case)
        created.append(case)
    db.flush()
    return created
