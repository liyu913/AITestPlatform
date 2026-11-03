"""Build HTTP assertions for API scenario steps from OpenAPI samples + LLM hints."""

from __future__ import annotations

from typing import Any

from app.services.api_automation.api_suite_steps import merge_assertions

_CODE_WHITELIST: set[Any] = {0, 200, "0", "00000", "200"}
_ENVELOPE_KEYS = ("data", "result", "message", "msg")
_NEST_ENVELOPES = ("data", "result")
_MAX_REQUIRED_EXISTS = 8
_ALLOWED_TYPES = {
    "status",
    "jsonpath",
    "contains",
    "not_contains",
    "regex",
    "header",
    "json_schema",
    "max_duration_ms",
}


def _ok_response(sample: dict[str, Any] | None) -> dict[str, Any] | None:
    responses = sample.get("responses") if isinstance(sample, dict) else None
    if not isinstance(responses, list):
        return None
    for row in responses:
        if not isinstance(row, dict):
            continue
        st = str(row.get("status") or "")
        if st.startswith("2"):
            return row
    return None


def _status_expected(ok: dict[str, Any] | None) -> int:
    if ok is None:
        return 200
    st = str(ok.get("status") or "").strip()
    if st.isdigit():
        return int(st)
    return 200


def _jsonpath_expr(name: str) -> str | None:
    n = (name or "").strip()
    if not n or n == "(root)":
        return None
    if n.startswith("$."):
        return n
    if n.startswith("$"):
        return n
    return f"$.{n}"


def _normalize_token(name: str) -> str:
    out: list[str] = []
    for ch in (name or "").strip():
        if ch.isupper() and out and out[-1] != "_":
            out.append("_")
        if ch.isalnum():
            out.append(ch.lower())
        elif ch in ("_", "-", "."):
            if not out or out[-1] != "_":
                out.append("_")
    return "".join(out).strip("_")


def _alias_candidates(hint: str) -> list[str]:
    """Expand businessy extract names (caseId / order_id) toward schema keys like id."""
    raw = (hint or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def add(value: str) -> None:
        v = (value or "").strip()
        if not v or v in seen:
            return
        seen.add(v)
        out.append(v)

    add(raw)
    norm = _normalize_token(raw)
    add(norm)
    # camelCase / snake_case last segment → id, token, etc.
    for sep in ("_", "."):
        if sep in norm:
            add(norm.split(sep)[-1])
    # caseId → id when suffix is Id
    if raw.endswith("Id") and len(raw) > 2:
        add("id")
    if raw.endswith("_id") and len(raw) > 3:
        add("id")
    if norm.endswith("_id") and len(norm) > 3:
        add("id")
    return out


def _collect_known_paths(sample: dict[str, Any] | None) -> tuple[set[str], dict[str, Any], str | None]:
    """Return (dotted field paths, body sample, primary nest envelope or None)."""
    ok = _ok_response(sample)
    sm = ok.get("sample") if ok and isinstance(ok.get("sample"), dict) else {}
    if not isinstance(sm, dict):
        sm = {}
    paths: set[str] = set()
    for key in sm:
        paths.add(str(key))
    for nest in _NEST_ENVELOPES:
        nested = sm.get(nest)
        if isinstance(nested, dict):
            for key in nested:
                paths.add(f"{nest}.{key}")
                paths.add(str(key))
    fields = ok.get("fields") if ok and isinstance(ok.get("fields"), list) else []
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name or name == "(root)":
            continue
        paths.add(name)
        if "." in name:
            paths.add(name.split(".")[-1])
    nest: str | None = None
    for key in _NEST_ENVELOPES:
        if key in sm and isinstance(sm.get(key), dict):
            nest = key
            break
    return paths, sm, nest


def _path_to_expr(path: str) -> str:
    return path if path.startswith("$") else f"$.{path}"


def _find_path_for_name(paths: set[str], name: str, nest: str | None) -> str | None:
    """Pick best dotted path for a candidate field name."""
    n = (name or "").strip()
    if not n:
        return None
    order = []
    if nest:
        order.append(nest)
    for nest_key in _NEST_ENVELOPES:
        if nest_key not in order:
            order.append(nest_key)
    for nest_key in order:
        candidate = f"{nest_key}.{n}"
        if candidate in paths:
            return candidate
    if n in paths:
        return n
    want = _normalize_token(n)
    # Prefer nested normalized matches, then bare leaf
    nested_match: str | None = None
    bare_match: str | None = None
    for path in paths:
        leaf = path.split(".")[-1]
        if _normalize_token(leaf) != want:
            continue
        if "." in path:
            nested_match = nested_match or path
        else:
            bare_match = bare_match or path
    return nested_match or bare_match


def resolve_extract_expr(sample: dict[str, Any] | None, hint: str) -> str:
    """Map an extract hint to a JSONPath based on response sample/fields.

    Prefer schema-aligned paths; never invent ``$.data.*`` when the body has no data envelope.
    """
    raw = str(hint or "").strip()
    if not raw:
        return "$.id"
    if raw.startswith("$"):
        return raw

    paths, _sm, nest = _collect_known_paths(sample)
    for candidate in _alias_candidates(raw):
        found = _find_path_for_name(paths, candidate, nest)
        if found:
            return _path_to_expr(found)

    if nest:
        return f"$.{nest}.{raw}"
    return f"$.{raw}"


def _rewrite_llm_jsonpath_expr(sample: dict[str, Any] | None, expr: str) -> str | None:
    """Drop or rewrite LLM jsonpath that assumes a missing data/result envelope."""
    e = (expr or "").strip()
    if not e.startswith("$."):
        return e
    paths, sm, nest = _collect_known_paths(sample)
    for env in _NEST_ENVELOPES:
        prefix = f"$.{env}."
        if not e.startswith(prefix):
            continue
        if env in sm and isinstance(sm.get(env), dict):
            return e
        # No such envelope in sample — rewrite via leaf / alias
        leaf = e[len(prefix) :]
        if not leaf or "." in leaf:
            # nested deeper without envelope evidence → drop
            return None
        rewritten = resolve_extract_expr(sample, leaf)
        # If still pointing at missing envelope, drop
        if rewritten.startswith(prefix) and env not in sm:
            return None
        return rewritten
    return e


def _sanitize_llm_assertions(raw: Any, sample: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "").strip().lower()
        if t not in _ALLOWED_TYPES:
            continue
        entry: dict[str, Any] = {"type": t}
        if item.get("expr") is not None:
            expr = str(item.get("expr") or "")
            if t == "jsonpath":
                rewritten = _rewrite_llm_jsonpath_expr(sample, expr)
                if rewritten is None:
                    continue
                entry["expr"] = rewritten
            else:
                entry["expr"] = expr
        if item.get("op") is not None:
            entry["op"] = str(item.get("op") or "eq")
        if "expected" in item:
            entry["expected"] = item.get("expected")
        out.append(entry)
    return out


def _code_whitelisted(code: Any) -> bool:
    if isinstance(code, bool):
        return False
    if code in (0, 200):
        return True
    return str(code) in {"0", "00000", "200"}


def schema_assertions(sample: dict[str, Any] | None, *, extract_hints: list[str] | None = None) -> list[dict[str, Any]]:
    ok = _ok_response(sample)
    assertions: list[dict[str, Any]] = [{"type": "status", "expected": _status_expected(ok)}]
    sm = ok.get("sample") if ok and isinstance(ok.get("sample"), dict) else {}
    if isinstance(sm, dict):
        if "success" in sm and isinstance(sm["success"], bool):
            assertions.append(
                {"type": "jsonpath", "expr": "$.success", "op": "eq", "expected": sm["success"]}
            )
        if "code" in sm and _code_whitelisted(sm["code"]):
            expected: Any = sm["code"]
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                expected = int(expected)
            assertions.append({"type": "jsonpath", "expr": "$.code", "op": "eq", "expected": expected})
        for key in _ENVELOPE_KEYS:
            if key in sm:
                assertions.append({"type": "jsonpath", "expr": f"$.{key}", "op": "exists"})

    fields = ok.get("fields") if ok and isinstance(ok.get("fields"), list) else []
    added = 0
    for field in fields:
        if added >= _MAX_REQUIRED_EXISTS:
            break
        if not isinstance(field, dict) or not field.get("required"):
            continue
        expr = _jsonpath_expr(str(field.get("name") or ""))
        if not expr:
            continue
        assertions.append({"type": "jsonpath", "expr": expr, "op": "exists"})
        added += 1

    for name in extract_hints or []:
        key = str(name or "").strip()
        if not key:
            continue
        assertions.append(
            {"type": "jsonpath", "expr": resolve_extract_expr(sample, key), "op": "exists"}
        )
    return assertions


def build_scenario_assertions(
    sample: dict[str, Any] | None,
    *,
    extract_hints: list[str] | None = None,
    llm_assertions: Any = None,
) -> list[dict[str, Any]]:
    base = schema_assertions(sample, extract_hints=extract_hints)
    extra = _sanitize_llm_assertions(llm_assertions, sample)
    if not extra:
        return merge_assertions(base, None)
    return merge_assertions(base, extra)


def response_assert_summary(sample: dict[str, Any] | None) -> dict[str, Any]:
    ok = _ok_response(sample)
    required: list[str] = []
    fields = ok.get("fields") if ok and isinstance(ok.get("fields"), list) else []
    for field in fields:
        if not isinstance(field, dict) or not field.get("required"):
            continue
        name = str(field.get("name") or "").strip()
        if name and name != "(root)":
            required.append(name)
    sm = ok.get("sample") if ok and isinstance(ok.get("sample"), dict) else {}
    envelope = [k for k in _ENVELOPE_KEYS if isinstance(sm, dict) and k in sm]
    envelope_eq: dict[str, Any] = {}
    if isinstance(sm, dict):
        if isinstance(sm.get("success"), bool):
            envelope_eq["success"] = sm["success"]
        if "code" in sm and _code_whitelisted(sm["code"]):
            code = sm["code"]
            envelope_eq["code"] = int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else code
    top_keys = [str(k) for k in sm.keys()][:16] if isinstance(sm, dict) else []
    return {
        "success_status": _status_expected(ok),
        "required_fields": required[:12],
        "envelope_keys": envelope,
        "envelope_eq": envelope_eq,
        "response_keys": top_keys,
    }


def sample_from_catalog_summary(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Rebuild a minimal responses sample so plan preview matches materialize rules."""
    if not isinstance(meta, dict):
        return {"responses": []}
    status = meta.get("success_status")
    st = str(int(status)) if isinstance(status, int) else str(status or "200")
    sm: dict[str, Any] = {}
    for k in meta.get("envelope_keys") or []:
        key = str(k)
        if key:
            sm[key] = {} if key in ("data", "result") else "ok"
    eq = meta.get("envelope_eq") if isinstance(meta.get("envelope_eq"), dict) else {}
    sm.update(eq)
    # Reconstruct top-level keys from required_fields / response_keys for flat bodies
    for n in meta.get("required_fields") or []:
        name = str(n).strip()
        if not name or name == "(root)":
            continue
        if "." in name:
            head, *rest = name.split(".")
            if head in ("data", "result"):
                bucket = sm.setdefault(head, {})
                if isinstance(bucket, dict) and rest:
                    bucket.setdefault(rest[0], None)
            continue
        if name not in sm:
            sm[name] = None
    for k in meta.get("response_keys") or []:
        key = str(k).strip()
        if key and key not in sm:
            sm[key] = None
    fields = [
        {"name": str(n), "required": True}
        for n in (meta.get("required_fields") or [])
        if str(n).strip()
    ]
    return {"responses": [{"status": st, "sample": sm, "fields": fields}]}
