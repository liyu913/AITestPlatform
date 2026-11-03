"""Normalize and evaluate ApiSuite steps_json (deterministic, no LLM)."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.api_automation.openapi_import import normalize_path_template


def steps_from_case_ids(case_ids: list[int], *, stop_on_fail: bool = True) -> list[dict[str, Any]]:
    """Build linear steps from legacy case_ids."""
    steps: list[dict[str, Any]] = []
    n = len(case_ids)
    for i, cid in enumerate(case_ids):
        sid = f"s{i + 1}"
        if i + 1 < n:
            on_pass = f"s{i + 2}"
            on_fail = "end" if stop_on_fail else f"s{i + 2}"
        else:
            on_pass = "end"
            on_fail = "end"
        steps.append(
            {
                "id": sid,
                "case_id": int(cid),
                "when": None,
                "on_pass": on_pass,
                "on_fail": on_fail,
            }
        )
    return steps


def _normalize_extract_overrides(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "type": str(item.get("type") or "jsonpath"),
            "expr": str(item.get("expr") or ""),
        }
        out.append(entry)
    return out or None


def merge_extractors(
    case_extractors: list[dict[str, Any]] | None,
    overrides: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Case extractors first; step overrides replace same name or append."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for ex in case_extractors or []:
        if not isinstance(ex, dict):
            continue
        name = str(ex.get("name") or "").strip()
        if not name:
            continue
        if name not in merged:
            order.append(name)
        merged[name] = dict(ex)
        merged[name]["name"] = name
    for ex in overrides or []:
        if not isinstance(ex, dict):
            continue
        name = str(ex.get("name") or "").strip()
        if not name:
            continue
        if name not in merged:
            order.append(name)
        merged[name] = {
            "name": name,
            "type": str(ex.get("type") or "jsonpath"),
            "expr": str(ex.get("expr") or ""),
        }
    return [merged[n] for n in order]


def _assertion_key(item: dict[str, Any]) -> str:
    t = str(item.get("type") or "").strip().lower()
    expr = str(item.get("expr") or "").strip()
    # status / duration / body-contains: one slot per type; jsonpath/header keyed by expr
    if t in ("status", "max_duration_ms", "contains", "not_contains"):
        return t
    return f"{t}:{expr}"


def _normalize_assert_overrides(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "").strip().lower()
        if not t:
            continue
        entry: dict[str, Any] = {"type": t}
        if item.get("expr") is not None:
            entry["expr"] = str(item.get("expr") or "")
        if item.get("op") is not None:
            entry["op"] = str(item.get("op") or "eq")
        if "expected" in item:
            entry["expected"] = item.get("expected")
        out.append(entry)
    return out or None


def merge_assertions(
    case_assertions: list[dict[str, Any]] | None,
    overrides: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Case assertions first; step overrides replace same key or append."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for a in case_assertions or []:
        if not isinstance(a, dict):
            continue
        key = _assertion_key(a)
        if not key or key == ":":
            continue
        if key not in merged:
            order.append(key)
        merged[key] = dict(a)
    for a in overrides or []:
        if not isinstance(a, dict):
            continue
        key = _assertion_key(a)
        if not key or key == ":":
            continue
        if key not in merged:
            order.append(key)
        merged[key] = dict(a)
    return [merged[k] for k in order]


def _as_str_dict(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(k).strip()}


def step_has_inline_request(step: dict[str, Any]) -> bool:
    method = str(step.get("method") or "").strip()
    path = str(step.get("path") or "").strip()
    return bool(method and path)


def normalize_steps(
    steps: list[dict[str, Any]] | None,
    case_ids: list[int] | None,
    *,
    stop_on_fail: bool = True,
) -> list[dict[str, Any]]:
    if isinstance(steps, list) and steps:
        out: list[dict[str, Any]] = []
        for i, raw in enumerate(steps):
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or f"s{i + 1}")
            case_id: int | None = None
            if raw.get("case_id") is not None:
                try:
                    case_id = int(raw["case_id"])
                except (TypeError, ValueError):
                    case_id = None
            method = str(raw.get("method") or "").strip().upper() or None
            path = str(raw.get("path") or "").strip() or None
            has_inline = bool(method and path)
            if case_id is None and not has_inline:
                continue
            on_pass = str(raw.get("on_pass") or "end")
            on_fail = str(raw.get("on_fail") or "end")
            when = raw.get("when")
            if when is not None and not isinstance(when, dict):
                when = None
            item: dict[str, Any] = {
                "id": sid,
                "when": when,
                "on_pass": on_pass,
                "on_fail": on_fail,
            }
            if case_id is not None:
                item["case_id"] = case_id
            if raw.get("rationale"):
                item["rationale"] = str(raw["rationale"])
            if raw.get("case_name"):
                item["case_name"] = str(raw["case_name"])
            if raw.get("name"):
                item["name"] = str(raw["name"])
            if raw.get("definition_id") is not None:
                try:
                    item["definition_id"] = int(raw["definition_id"])
                except (TypeError, ValueError):
                    pass
            if raw.get("definition_name"):
                item["definition_name"] = str(raw["definition_name"])
            if has_inline:
                item["method"] = method
                item["path"] = path
                item["query"] = _as_str_dict(raw.get("query"))
                item["headers"] = _as_str_dict(raw.get("headers"))
                item["body_type"] = str(raw.get("body_type") or "none")
                item["body"] = str(raw.get("body") or "")
                asserts = _normalize_assert_overrides(raw.get("assertions"))
                item["assertions"] = asserts or []
                extractors = _normalize_extract_overrides(raw.get("extractors"))
                item["extractors"] = extractors or []
                try:
                    timeout = int(raw.get("timeout_ms") or 15000)
                except (TypeError, ValueError):
                    timeout = 15000
                item["timeout_ms"] = max(1000, min(120000, timeout))
            overrides = _normalize_extract_overrides(raw.get("extract_overrides"))
            if overrides:
                item["extract_overrides"] = overrides
            assert_overrides = _normalize_assert_overrides(raw.get("assert_overrides"))
            if assert_overrides:
                item["assert_overrides"] = assert_overrides
            out.append(item)
        return out
    ids = [int(x) for x in (case_ids or [])]
    return steps_from_case_ids(ids, stop_on_fail=stop_on_fail)


def case_ids_from_steps(steps: list[dict[str, Any]]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for s in steps:
        try:
            cid = int(s["case_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _render_left(left: str, ctx: dict[str, str], *, last_status: int | None) -> str:
    text = str(left or "")
    if text in ("{{status}}", "{{response_status}}") and last_status is not None:
        return str(last_status)
    # simple {{var}} replace from ctx
    if text.startswith("{{") and text.endswith("}}"):
        key = text[2:-2].strip()
        if key in ctx:
            return str(ctx[key])
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def eval_when(
    when: dict[str, Any] | None,
    *,
    ctx: dict[str, str],
    last_status: int | None = None,
) -> bool:
    """Return True if step should run. None/empty when → True."""
    if not when or not isinstance(when, dict):
        return True
    op = str(when.get("op") or "eq").lower()
    left_raw = when.get("left", "")
    right = when.get("right")
    left = _render_left(str(left_raw), ctx, last_status=last_status)

    if op == "exists":
        key = str(left_raw).strip("{} ").strip()
        return key in ctx and str(ctx.get(key, "")).strip() != ""
    if op == "eq":
        return str(left) == str(right)
    if op == "ne":
        return str(left) != str(right)
    if op == "contains":
        return str(right) in str(left)
    return True


def apply_consume_hints_to_payload(
    payload: dict[str, Any],
    hints: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Mutate a copy of step request payload using plan consume_hints."""
    out = dict(payload)
    headers = dict(out.get("headers") or {})
    query = dict(out.get("query") or {})
    path = str(out.get("path") or "/")
    body = str(out.get("body") or "")
    body_type = str(out.get("body_type") or "none")

    for h in hints or []:
        if not isinstance(h, dict):
            continue
        var = str(h.get("var") or "").strip()
        if not var:
            continue
        dest = str(h.get("in") or "header").strip().lower()
        key = str(h.get("key") or var).strip() or var
        template = str(h.get("template") or ("{{" + var + "}}"))
        if dest == "header":
            headers[key] = template
        elif dest == "query":
            query[key] = template
        elif dest == "path":
            # Prefer already-doubled `{{key}}`; avoid turning it into `{{{key}}}`.
            doubled = "{{" + key + "}}"
            target = "{{" + var + "}}"
            if doubled in path:
                path = path.replace(doubled, target)
            else:
                path = re.sub(
                    r"(?<!\{)\{" + re.escape(key) + r"\}(?!\})",
                    target,
                    path,
                )
            path = path.replace(":" + key, target)
            path = normalize_path_template(path)
        elif dest == "body":
            if body_type in ("none", "", "json") or body.strip().startswith("{"):
                try:
                    obj = json.loads(body) if body.strip() else {}
                except json.JSONDecodeError:
                    obj = {}
                if isinstance(obj, dict):
                    # store template string; runtime {{var}} replace happens on text
                    obj[key] = template
                    body = json.dumps(obj, ensure_ascii=False)
                    body_type = "json"
            else:
                # text body: append key=template line if empty-ish
                if not body.strip():
                    body = f"{key}={template}"
                    body_type = "text"

    out["headers"] = headers
    out["query"] = query
    out["path"] = path
    out["body"] = body
    out["body_type"] = body_type
    return out


def _normalize_consume_hints(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        var = str(item.get("var") or "").strip()
        if not var:
            continue
        dest = str(item.get("in") or "header").strip().lower()
        if dest not in ("header", "query", "path", "body"):
            dest = "header"
        entry: dict[str, Any] = {
            "var": var,
            "in": dest,
            "key": str(item.get("key") or var).strip() or var,
            "template": str(item.get("template") or ("{{" + var + "}}")),
        }
        if item.get("from_step"):
            entry["from_step"] = str(item["from_step"])
        out.append(entry)
    return out or None


def normalize_definition_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize plan steps keyed by definition_id (pre-materialize)."""
    if not isinstance(steps, list) or not steps:
        return []
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or f"s{i + 1}")
        try:
            definition_id = int(raw["definition_id"])
        except (KeyError, TypeError, ValueError):
            continue
        when = raw.get("when")
        if when is not None and not isinstance(when, dict):
            when = None
        item: dict[str, Any] = {
            "id": sid,
            "definition_id": definition_id,
            "when": when,
            "on_pass": str(raw.get("on_pass") or "end"),
            "on_fail": str(raw.get("on_fail") or "end"),
        }
        for key in ("definition_name", "method", "path", "rationale", "case_name"):
            if raw.get(key) is not None:
                item[key] = str(raw[key])
        if isinstance(raw.get("extract_hints"), list):
            item["extract_hints"] = [str(x).strip() for x in raw["extract_hints"] if str(x or "").strip()]
        consume = _normalize_consume_hints(raw.get("consume_hints"))
        if consume:
            item["consume_hints"] = consume
        asserts = _normalize_assert_overrides(raw.get("assertions"))
        if asserts:
            item["assertions"] = asserts
        # optional pre-bound case_id (after materialize / reuse)
        if raw.get("case_id") is not None:
            try:
                item["case_id"] = int(raw["case_id"])
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def validate_steps_graph(
    steps: list[dict[str, Any]],
    *,
    allowed_case_ids: set[int] | None = None,
    allowed_definition_ids: set[int] | None = None,
    id_field: str = "case_id",
) -> list[str]:
    """Return list of error messages (empty = ok)."""
    errors: list[str] = []
    if not steps:
        errors.append("步骤为空")
        return errors
    ids = {str(s.get("id")) for s in steps}
    for s in steps:
        sid = str(s.get("id"))
        if id_field == "definition_id":
            try:
                ref_id = int(s[id_field])
            except (KeyError, TypeError, ValueError):
                errors.append(f"步骤 {sid} 缺少合法 {id_field}")
                continue
            if allowed_definition_ids is not None and ref_id not in allowed_definition_ids:
                errors.append(f"步骤 {sid} 引用了非候选接口 {ref_id}")
        else:
            # Suite runtime steps: inline request and/or legacy case_id
            has_inline = step_has_inline_request(s)
            case_id: int | None = None
            if s.get("case_id") is not None:
                try:
                    case_id = int(s["case_id"])
                except (TypeError, ValueError):
                    case_id = None
            if case_id is None and not has_inline:
                errors.append(f"步骤 {sid} 缺少可执行请求（method/path）或 case_id")
            elif case_id is not None and allowed_case_ids is not None and case_id not in allowed_case_ids:
                errors.append(f"步骤 {sid} 引用了非候选用例 {case_id}")
        for edge in ("on_pass", "on_fail"):
            nxt = str(s.get(edge) or "end")
            if nxt != "end" and nxt not in ids:
                errors.append(f"步骤 {sid} 的 {edge}={nxt} 不存在")
    by_id = {str(s["id"]): s for s in steps}
    start = str(steps[0]["id"])
    seen: set[str] = set()
    cur = start
    hops = 0
    max_hops = max(len(steps) * 3, 10)
    while cur != "end" and hops < max_hops:
        if cur in seen and hops > len(steps):
            break
        seen.add(cur)
        node = by_id.get(cur)
        if node is None:
            errors.append(f"无法从 {start} 走到步骤 {cur}")
            break
        cur = str(node.get("on_pass") or "end")
        hops += 1
    if hops >= max_hops:
        errors.append("步骤图可能存在环")
    return errors
