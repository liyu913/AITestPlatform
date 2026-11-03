"""HTTP API case runner: template render, scripts, request, assert, extract."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.services.api_automation.api_scripts import ScriptError, run_api_script
from app.services.api_automation.api_url_guard import UnsafeApiUrlError, join_base_and_path

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
RESPONSE_BODY_LIMIT = 65536


class MissingVariableError(ValueError):
    pass


def render_template(text: str, ctx: dict[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in ctx:
            raise MissingVariableError(f"缺少变量: {key}")
        return ctx[key]

    return _VAR_RE.sub(repl, text)


def render_mapping(data: dict[str, Any] | None, ctx: dict[str, str]) -> dict[str, str]:
    if not data:
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        out[str(k)] = render_template(str(v), ctx)
    return out


def resolve_jsonpath(data: Any, expr: str) -> Any:
    e = (expr or "").strip()
    if not e.startswith("$"):
        raise ValueError("jsonpath 必须以 $ 开头")
    cur: Any = data
    rest = e[1:]
    if rest.startswith("."):
        rest = rest[1:]
    if not rest:
        return cur
    rest = re.sub(r"\[(\d+)\]", r".\1", rest)
    parts = [p for p in rest.split(".") if p]
    for part in parts:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError(part)
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


def truncate_body(text: str, limit: int = RESPONSE_BODY_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(truncated, total {len(text)} chars)"


def _validate_json_schema(instance: Any, schema: Any) -> None:
    try:
        import jsonschema  # type: ignore
    except ImportError as ex:
        raise ValueError("服务器未安装 jsonschema，无法校验 JSON Schema") from ex
    jsonschema.validate(instance=instance, schema=schema)


def run_assertions(
    assertions: list[dict[str, Any]] | None,
    *,
    status_code: int,
    body_text: str,
    body_json: Any,
    headers: dict[str, Any] | None,
    duration_ms: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for raw in assertions or []:
        atype = raw.get("type")
        ok = False
        actual: Any = None
        detail = ""
        try:
            if atype == "status":
                expected = raw.get("expected")
                actual = status_code
                ok = status_code == expected
                detail = f"status {status_code} == {expected}"
            elif atype == "max_duration_ms":
                expected = int(raw.get("expected") or 0)
                actual = duration_ms
                ok = duration_ms <= expected
                detail = f"duration {duration_ms}ms <= {expected}ms"
            elif atype == "jsonpath":
                expr = raw.get("expr") or ""
                op = raw.get("op") or "eq"
                actual = resolve_jsonpath(body_json, expr)
                if op == "exists":
                    ok = True
                    detail = f"{expr} exists"
                elif op == "contains":
                    expected = raw.get("expected")
                    ok = str(expected) in str(actual)
                    detail = f"{expr} contains {expected!r} (actual={actual!r})"
                elif op == "regex":
                    pattern = str(raw.get("expected") or "")
                    ok = re.search(pattern, str(actual)) is not None
                    detail = f"{expr} =~ /{pattern}/ (actual={actual!r})"
                else:
                    expected = raw.get("expected")
                    ok = actual == expected
                    detail = f"{expr} == {expected!r} (actual={actual!r})"
            elif atype == "contains":
                expected = str(raw.get("expected") or "")
                actual = body_text
                ok = expected in body_text
                detail = f"body contains {expected!r}"
            elif atype == "not_contains":
                expected = str(raw.get("expected") or "")
                actual = body_text
                ok = expected not in body_text
                detail = f"body not contains {expected!r}"
            elif atype == "regex":
                pattern = str(raw.get("expected") or raw.get("expr") or "")
                actual = body_text
                ok = re.search(pattern, body_text, re.S) is not None
                detail = f"body =~ /{pattern}/"
            elif atype == "header":
                name = str(raw.get("expr") or "").lower()
                actual = hdrs.get(name)
                op = raw.get("op") or "eq"
                if op == "exists":
                    ok = actual is not None
                    detail = f"header {name} exists"
                else:
                    expected = str(raw.get("expected") or "")
                    ok = (actual or "") == expected
                    detail = f"header {name} == {expected!r} (actual={actual!r})"
            elif atype == "json_schema":
                schema = raw.get("expected")
                if isinstance(schema, str):
                    schema = json.loads(schema)
                _validate_json_schema(body_json, schema)
                ok = True
                detail = "json_schema valid"
                actual = "ok"
            else:
                detail = f"未知断言类型: {atype}"
                ok = False
        except Exception as ex:  # noqa: BLE001
            ok = False
            detail = str(ex)
        results.append(
            {
                "type": atype,
                "ok": ok,
                "detail": detail,
                "actual": actual,
                "expected": raw.get("expected"),
                "expr": raw.get("expr"),
            }
        )
    return results


def apply_extractors(
    extractors: list[dict[str, Any]] | None,
    body_json: Any,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for ex in extractors or []:
        name = str(ex.get("name") or "").strip()
        if not name:
            continue
        expr = ex.get("expr") or ""
        val = resolve_jsonpath(body_json, expr)
        out[name] = "" if val is None else str(val)
    return out


def execute_http_case(
    *,
    method: str,
    base_url: str,
    path: str,
    query: dict[str, Any] | None,
    headers: dict[str, Any] | None,
    body_type: str,
    body_text: str,
    timeout_ms: int,
    assertions: list[dict[str, Any]] | None,
    extractors: list[dict[str, Any]] | None,
    ctx: dict[str, str],
    pre_script: str | None = None,
    post_script: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute one case; returns step result dict. Mutates ctx with extractors/scripts."""
    req_mut: dict[str, Any] = {
        "method": method,
        "path": path,
        "query": dict(query or {}),
        "headers": dict(headers or {}),
        "body_type": body_type,
        "body": body_text or "",
    }
    try:
        run_api_script(pre_script, ctx=ctx, request=req_mut)
        method = str(req_mut.get("method") or method)
        path = str(req_mut.get("path") or path)
        query = req_mut.get("query") if isinstance(req_mut.get("query"), dict) else {}
        headers = req_mut.get("headers") if isinstance(req_mut.get("headers"), dict) else {}
        body_type = str(req_mut.get("body_type") or body_type)
        body_text = str(req_mut.get("body") or "")

        rendered_path = render_template(path, ctx)
        rendered_query = render_mapping(query, ctx)
        rendered_headers = render_mapping(headers, ctx)
        url = join_base_and_path(base_url, rendered_path)
        content: str | None = None
        json_body: Any = None
        if body_type == "json" and body_text.strip():
            rendered_body = render_template(body_text, ctx)
            json_body = json.loads(rendered_body)
        elif body_type == "text" and body_text:
            content = render_template(body_text, ctx)
    except (MissingVariableError, UnsafeApiUrlError, json.JSONDecodeError, ValueError, ScriptError) as ex:
        return {
            "status": "error",
            "error_message": str(ex),
            "request_snapshot": None,
            "response_status": None,
            "response_headers": None,
            "response_body": "",
            "duration_ms": None,
            "assertion_results": [],
            "extract_results": {},
        }

    snapshot = {
        "method": method.upper(),
        "url": url,
        "query": rendered_query,
        "headers": {k: ("***" if k.lower() == "authorization" else v) for k, v in rendered_headers.items()},
        "body_type": body_type,
        "body": json_body if body_type == "json" else content,
    }

    own_client = client is None
    http = client or httpx.Client(timeout=timeout_ms / 1000.0, follow_redirects=False)
    t0 = time.perf_counter()
    try:
        resp = http.request(
            method.upper(),
            url,
            params=rendered_query or None,
            headers=rendered_headers or None,
            json=json_body if body_type == "json" and json_body is not None else None,
            content=content if body_type == "text" else None,
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        body_text_out = truncate_body(resp.text)
        try:
            body_json: Any = resp.json()
        except Exception:  # noqa: BLE001
            body_json = None

        assertion_results = run_assertions(
            assertions,
            status_code=resp.status_code,
            body_text=resp.text,
            body_json=body_json,
            headers=dict(resp.headers),
            duration_ms=duration_ms,
        )
        extract_results: dict[str, str] = {}
        if body_json is not None:
            try:
                extract_results = apply_extractors(extractors, body_json)
                ctx.update(extract_results)
            except Exception as ex:  # noqa: BLE001
                return {
                    "status": "error",
                    "error_message": f"提取变量失败: {ex}",
                    "request_snapshot": snapshot,
                    "response_status": resp.status_code,
                    "response_headers": dict(resp.headers),
                    "response_body": body_text_out,
                    "duration_ms": duration_ms,
                    "assertion_results": assertion_results,
                    "extract_results": {},
                }

        try:
            run_api_script(
                post_script,
                ctx=ctx,
                response={
                    "status": resp.status_code,
                    "headers": dict(resp.headers),
                    "body": resp.text,
                    "json": body_json,
                    "duration_ms": duration_ms,
                },
            )
        except ScriptError as ex:
            return {
                "status": "error",
                "error_message": str(ex),
                "request_snapshot": snapshot,
                "response_status": resp.status_code,
                "response_headers": dict(resp.headers),
                "response_body": body_text_out,
                "duration_ms": duration_ms,
                "assertion_results": assertion_results,
                "extract_results": extract_results,
            }

        all_ok = all(r.get("ok") for r in assertion_results) if assertion_results else True
        return {
            "status": "passed" if all_ok else "failed",
            "error_message": "" if all_ok else "断言失败",
            "request_snapshot": snapshot,
            "response_status": resp.status_code,
            "response_headers": dict(resp.headers),
            "response_body": body_text_out,
            "duration_ms": duration_ms,
            "assertion_results": assertion_results,
            "extract_results": extract_results,
        }
    except httpx.HTTPError as ex:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "status": "error",
            "error_message": f"请求失败: {ex}",
            "request_snapshot": snapshot,
            "response_status": None,
            "response_headers": None,
            "response_body": "",
            "duration_ms": duration_ms,
            "assertion_results": [],
            "extract_results": {},
        }
    finally:
        if own_client:
            http.close()
