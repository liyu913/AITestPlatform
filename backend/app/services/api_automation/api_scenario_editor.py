"""Revise an existing API scenario's steps from a natural-language instruction.

The model must return a full steps array. Fields it omits on an existing step are
kept from the editor payload. New steps must reference a project API definition;
the definition template fills the request, then explicit model fields overlay it.
Nothing is persisted and API definitions are not modified.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.api_automation.api_automation import ApiDefinition
from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.api_automation.api_assertions import resolve_extract_expr
from app.services.api_automation.api_case_gen import _sample_context
from app.services.api_automation.api_definitions import definition_to_step_payload
from app.services.api_automation.api_scenario_llm import llm_json
from app.services.api_automation.api_suite_steps import (
    apply_consume_hints_to_payload,
    normalize_steps,
    validate_steps_graph,
)

log = logging.getLogger(__name__)

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_BODY_TYPES = {"none", "json", "text"}
_WHEN_OPS = {"eq", "ne", "contains", "exists"}
_OVERLAY_KEYS = (
    "name",
    "definition_name",
    "method",
    "path",
    "query",
    "headers",
    "body_type",
    "body",
    "assertions",
    "extractors",
    "timeout_ms",
    "when",
    "on_pass",
    "on_fail",
    "rationale",
    "case_name",
)
_NULLABLE_OVERLAY = {"when", "rationale"}


def _system_prompt() -> str:
    return (
        "你是接口场景编排编辑助手。用户正在编辑一条已有场景的测试步骤，"
        "请按修改要求调整步骤上的请求、断言、提取变量和控制流。"
        "只输出一个 JSON 对象，不要 Markdown。格式："
        '{"summary":"一句话说明改了什么","warnings":[],"steps":['
        '{"id":"s1","definition_id":101,"name":"...","method":"POST","path":"/x",'
        '"query":{},"headers":{},"body_type":"json","body":"{}",'
        '"assertions":[{"type":"status","expected":201},'
        '{"type":"jsonpath","expr":"$.id","op":"exists"}],'
        '"extractors":[{"name":"id","type":"jsonpath","expr":"$.id"}],'
        '"timeout_ms":15000,"when":null,"on_pass":"s2","on_fail":"end","rationale":"...",'
        '"consume_hints":[{"var":"id","from_step":"s1","in":"path","key":"id","template":"{{id}}"}]'
        "}]}"
        "约束："
        "必须返回修订后的完整 steps，不要只返回 diff；"
        "用户未要求改动的步骤原样保留，未改的字段请直接省略（尤其 body、headers、query、assertions、extractors）；"
        "省略的字段会沿用当前步骤，不要把未提及的步骤清空；"
        "新增或更换接口的步骤必须使用目录中的 definition_id，禁止编造接口，也不要改接口目录本身；"
        "id 在本次 steps 内唯一；on_pass/on_fail 只能是其它步骤 id 或 end；"
        "when 为 null 或 {\"op\":\"eq|ne|contains|exists\",\"left\":\"{{var}}\",\"right\":\"...\"}；"
        "assertions 的 type 仅用 status、jsonpath、contains、not_contains、regex、header、max_duration_ms、json_schema；"
        "jsonpath 的 op 为 eq|exists|contains|regex；"
        "assertions/extractors 的 jsonpath 必须对齐真实响应结构："
        "扁平响应用 $.id 等顶层路径；仅当响应确有 data 信封时才写 $.data.x；禁止臆造 $.data.caseId；"
        "consume_hints 的 in 为 header|query|path|body，无传参则省略 consume_hints。"
    )


def _catalog(definitions: list[ApiDefinition]) -> list[dict[str, Any]]:
    shown = definitions[:200]
    return [
        {
            "id": int(row.id),
            "name": row.name,
            "method": row.method,
            "path": row.path,
        }
        for row in shown
    ]


def _load_definitions(db: Session, project_id: int) -> dict[int, ApiDefinition]:
    rows = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.project_id == project_id)
        .order_by(ApiDefinition.id.asc())
        .limit(500)
        .all()
    )
    return {int(row.id): row for row in rows}


def _definition(
    db: Session,
    project_id: int,
    by_id: dict[int, ApiDefinition],
    definition_id: int,
) -> ApiDefinition | None:
    row = by_id.get(definition_id)
    if row is not None:
        return row
    return (
        db.query(ApiDefinition)
        .filter(ApiDefinition.id == definition_id, ApiDefinition.project_id == project_id)
        .first()
    )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _overlay(base: dict[str, Any], proposed: dict[str, Any], warnings: list[str], step_id: str) -> dict[str, Any]:
    out = dict(base)
    for key in _OVERLAY_KEYS:
        if key not in proposed:
            continue
        value = proposed[key]
        if value is None and key not in _NULLABLE_OVERLAY:
            continue
        if key in ("query", "headers", "assertions", "extractors") and value is not None and not isinstance(
            value, (dict, list)
        ):
            warnings.append(f"步骤 {step_id} 的 {key} 格式不正确，已忽略")
            continue
        if key in ("query", "headers") and isinstance(value, list):
            warnings.append(f"步骤 {step_id} 的 {key} 格式不正确，已忽略")
            continue
        if key in ("assertions", "extractors") and isinstance(value, dict):
            warnings.append(f"步骤 {step_id} 的 {key} 格式不正确，已忽略")
            continue
        out[key] = value
    return out


def _apply_extract_hints(
    step: dict[str, Any],
    proposed: dict[str, Any],
    *,
    sample: dict[str, Any] | None = None,
) -> None:
    if "extractors" in proposed and proposed.get("extractors") is not None:
        return
    hints = proposed.get("extract_hints")
    if not isinstance(hints, list):
        return
    extractors = list(step.get("extractors") or [])
    names = {str(item.get("name")) for item in extractors if isinstance(item, dict)}
    for hint in hints:
        name = str(hint or "").strip()
        if not name or name in names:
            continue
        extractors.append(
            {"name": name, "type": "jsonpath", "expr": resolve_extract_expr(sample, name)}
        )
        names.add(name)
    step["extractors"] = extractors


def _check_when(step_id: str, when: Any) -> str | None:
    if when is None:
        return None
    if not isinstance(when, dict):
        return f"步骤 {step_id} 的 when 不合法"
    op = str(when.get("op") or "")
    if op not in _WHEN_OPS:
        return f"步骤 {step_id} 的 when.op 不合法"
    return None


def _history(messages: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in (messages or [])[-8:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        out.append({"role": role, "content": content[:4000]})
    return out


def merge_revised_steps(
    original_steps: list[dict[str, Any]],
    proposed_steps: list[Any],
    *,
    db: Session,
    project_id: int,
    definitions: dict[int, ApiDefinition],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return normalized steps and warnings, or raise AppError without a partial result."""
    warnings: list[str] = []
    errors: list[str] = []
    by_original = {str(step.get("id")): step for step in original_steps if isinstance(step, dict)}
    built: list[dict[str, Any]] = []
    seen: set[str] = set()

    if not isinstance(proposed_steps, list) or not proposed_steps:
        raise AppError("bad_request", "模型未返回有效步骤", status_code=400)

    for index, raw in enumerate(proposed_steps):
        if not isinstance(raw, dict):
            errors.append(f"第 {index + 1} 个步骤不是对象")
            continue
        step_id = str(raw.get("id") or "").strip()
        if not step_id:
            errors.append(f"第 {index + 1} 个步骤缺少 id")
            continue
        if step_id in seen:
            errors.append(f"步骤 id 重复: {step_id}")
            continue
        seen.add(step_id)

        original = by_original.get(step_id)
        proposed_def = _as_int(raw.get("definition_id")) if "definition_id" in raw else None
        has_def = "definition_id" in raw and raw.get("definition_id") is not None
        if has_def and proposed_def is None:
            errors.append(f"步骤 {step_id} 的 definition_id 不合法")
            continue

        rebound = False
        if original is None:
            if proposed_def is None:
                errors.append(f"步骤 {step_id} 是新增步骤，必须引用项目内已有接口")
                continue
            definition = _definition(db, project_id, definitions, proposed_def)
            if definition is None:
                errors.append(f"步骤 {step_id} 引用了不存在的接口 {proposed_def}")
                continue
            base = definition_to_step_payload(definition)
            base["id"] = step_id
            base["on_pass"] = "end"
            base["on_fail"] = "end"
            base["when"] = None
            rebound = True
        else:
            original_def = _as_int(original.get("definition_id"))
            if has_def and proposed_def != original_def:
                definition = _definition(db, project_id, definitions, int(proposed_def))
                if definition is None:
                    errors.append(f"步骤 {step_id} 引用了不存在的接口 {proposed_def}")
                    continue
                base = definition_to_step_payload(definition)
                base["id"] = step_id
                base["on_pass"] = original.get("on_pass") or "end"
                base["on_fail"] = original.get("on_fail") or "end"
                base["when"] = original.get("when")
                rebound = True
            else:
                base = dict(original)
                base["id"] = step_id

        merged = _overlay(base, raw, warnings, step_id)
        merged["id"] = step_id
        if rebound:
            merged.pop("case_id", None)
        elif original and original.get("case_id") is not None:
            merged["case_id"] = original.get("case_id")
        else:
            merged.pop("case_id", None)

        def_id_for_sample = _as_int(merged.get("definition_id"))
        sample_ctx = None
        if def_id_for_sample is not None:
            def_row = _definition(db, project_id, definitions, def_id_for_sample)
            if def_row is not None:
                sample_ctx = _sample_context(db, def_row)
        _apply_extract_hints(merged, raw, sample=sample_ctx)
        if isinstance(raw.get("consume_hints"), list):
            merged = apply_consume_hints_to_payload(merged, raw.get("consume_hints"))

        when_err = _check_when(step_id, merged.get("when"))
        if when_err:
            errors.append(when_err)
            continue

        method = str(merged.get("method") or "").strip().upper()
        path = str(merged.get("path") or "").strip()
        if method and method not in _METHODS:
            errors.append(f"步骤 {step_id} 的 method 不合法")
            continue
        body_type = str(merged.get("body_type") or "none").strip().lower()
        if method and path:
            if body_type not in _BODY_TYPES:
                errors.append(f"步骤 {step_id} 的 body_type 不合法")
                continue
            merged["method"] = method
            merged["path"] = path
            merged["body_type"] = body_type
        elif merged.get("case_id") is None:
            errors.append(f"步骤 {step_id} 缺少可执行请求（method/path）")
            continue
        built.append(merged)

    if errors:
        raise AppError("bad_request", "; ".join(errors), status_code=400)

    steps = normalize_steps(built, None)
    if len(steps) != len(built):
        raise AppError("bad_request", "有步骤缺少可执行请求，未应用修订", status_code=400)
    graph_errors = validate_steps_graph(steps)
    if graph_errors:
        raise AppError("bad_request", "; ".join(graph_errors), status_code=400)
    return steps, warnings


def revise_scenario_steps(
    db: Session,
    *,
    project_id: int,
    message: str,
    steps: list[dict[str, Any]],
    messages: list[dict[str, Any]] | None,
    llm_cfg: LlmRuntime,
) -> dict[str, Any]:
    definitions = _load_definitions(db, project_id)
    catalog = _catalog(list(definitions.values()))
    user_content = (
        f"项目接口目录（新增步骤只能使用这些 id）：\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"当前场景步骤 JSON：\n{json.dumps(steps, ensure_ascii=False)}\n\n"
        f"修改要求：\n{message.strip()}\n\n"
        "请输出修订后的完整 JSON。"
    )
    llm_messages: list[dict[str, Any]] = [{"role": "system", "content": _system_prompt()}]
    llm_messages.extend(_history(messages))
    llm_messages.append({"role": "user", "content": user_content})
    try:
        plan = llm_json(llm_messages, cfg=llm_cfg, scene="场景编排修订")
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("scenario editor revise failed")
        raise AppError("bad_request", f"修订步骤失败: {exc}", status_code=400) from exc

    if not isinstance(plan, dict):
        raise AppError("bad_request", "模型未返回 JSON 对象", status_code=400)
    summary = str(plan.get("summary") or "").strip() or "已修订场景步骤"
    model_warnings = plan.get("warnings") if isinstance(plan.get("warnings"), list) else []
    warning_text = [str(item).strip() for item in model_warnings if str(item or "").strip()]
    revised, merge_warnings = merge_revised_steps(
        steps,
        plan.get("steps"),
        db=db,
        project_id=project_id,
        definitions=definitions,
    )
    return {
        "summary": summary[:2000],
        "warnings": (warning_text + merge_warnings)[:20],
        "steps": revised,
    }
