"""LangGraph: plan-first single-API case agent (multi-def matrices + confirm)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session

from app.models.api_automation.api_automation import (
    ApiCase,
    ApiCaseAgentSession,
    ApiDefinition,
)
from app.services.api_automation.api_assertions import response_assert_summary
from app.services.api_automation.api_case_gen import _sample_context, build_case_blueprints
from app.services.api_automation.api_scenario_events import emit_log, get_event_bus
from app.services.api_automation.api_scenario_llm import llm_json
from app.services.ai.effective_ai_settings import LlmRuntime

log = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_compiled = None
_llm_by_thread: dict[str, LlmRuntime] = {}

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_MAX_CASES_PER_DEF = 12
_MAX_GROUPS = 30


class CaseAgentState(TypedDict, total=False):
    project_id: int
    user_id: int
    prompt: str
    candidate_definitions: list[dict[str, Any]]
    candidate_definition_ids: list[int]
    seed_groups: list[dict[str, Any]]
    messages: list[dict[str, str]]
    plan: dict[str, Any] | None
    user_decision: str | None
    revise_message: str | None
    created_case_ids: list[int] | None
    run_id: int | None
    error: str | None
    thread_id: str


def bind_llm(thread_id: str, cfg: LlmRuntime) -> None:
    _llm_by_thread[thread_id] = cfg


def unbind_llm(thread_id: str) -> None:
    _llm_by_thread.pop(thread_id, None)


def _system_prompt() -> str:
    return (
        "你是接口「单接口用例」设计助手。根据候选接口定义与规则种子用例，"
        "为每个接口设计独立的用例矩阵（正向 / 异常 / 边界），不要把多个接口串成场景。"
        "只输出一个 JSON 对象，不要 Markdown。格式："
        '{"title":"...","summary":"...","groups":['
        '{"definition_id":101,"definition_name":"...","method":"POST","path":"/x",'
        '"cases":[{"name":"...","tags":["positive"],"method":"POST","path":"/x",'
        '"query":{},"headers":{},"body_type":"json","body":"{...}",'
        '"assertions":[{"type":"status","expected":200}],'
        '"extractors":[{"name":"token","type":"jsonpath","expr":"$.data.token"}],'
        '"timeout_ms":15000,"description":"...","env_vars_needed":["token"],'
        '"rationale":"..."}]'
        '}],"warnings":[],"needs_user_input":[]}'
        "设计规则："
        "1) 每个接口至少含正向-正常请求；写接口尽量覆盖缺必填、空Body、非法JSON、类型错误、空串/超长边界；"
        "2) 接口之间禁止步骤串联与 consume；跨接口依赖用 {{var}} 占位，并写入 env_vars_needed / warnings；"
        "3) 有数据依赖时引导「配置环境变量」或「改用场景 Agent」，不要伪造多步前置；"
        "4) 正向成功用例可提取常用字段（token、id 等）到 extractors，异常/边界一般不提取；"
        "5) definition_id 必须来自候选；method/path 与接口一致；每个接口 cases 不超过 12 条；"
        "6) 断言优先 status + 信封字段 exists/eq；禁止编造随机 expected；"
        "7) tags 使用 auto-gen / positive / negative / boundary / required / type / auth 等。"
    )


def _log(state: CaseAgentState, node: str, message: str, level: str = "info", **data: Any) -> None:
    tid = str(state.get("thread_id") or "")
    if not tid:
        return
    emit_log(tid, node=node, message=message, level=level, data=data or None)


def _as_str_dict(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _extract_env_vars(*texts: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for m in _VAR_RE.finditer(text or ""):
            key = m.group(1)
            if key not in seen:
                seen.add(key)
                names.append(key)
    return names


def _blueprint_to_case(bp: dict[str, Any]) -> dict[str, Any]:
    body = str(bp.get("body") or "")
    headers = _as_str_dict(bp.get("headers"))
    query = _as_str_dict(bp.get("query"))
    path = str(bp.get("path") or "/")
    env_vars = _extract_env_vars(
        path,
        body,
        json.dumps(headers, ensure_ascii=False),
        json.dumps(query, ensure_ascii=False),
    )
    tags = [str(t) for t in (bp.get("tags") or []) if str(t or "").strip()]
    if "auto-gen" not in tags:
        tags = ["auto-gen", *tags]
    return {
        "name": str(bp.get("name") or "未命名用例")[:512],
        "tags": tags,
        "method": str(bp.get("method") or "GET").upper(),
        "path": path,
        "query": query,
        "headers": headers,
        "body_type": str(bp.get("body_type") or "none"),
        "body": body,
        "assertions": list(bp.get("assertions") or []),
        "extractors": list(bp.get("extractors") or []),
        "timeout_ms": int(bp.get("timeout_ms") or 15000),
        "description": str(bp.get("description") or ""),
        "env_vars_needed": env_vars,
        "rationale": str(bp.get("description") or ""),
    }


def _seed_group_from_definition(db: Session, definition: ApiDefinition) -> dict[str, Any]:
    sample = _sample_context(db, definition)
    blueprints = build_case_blueprints(definition, sample)[:_MAX_CASES_PER_DEF]
    cases = [_blueprint_to_case(bp) for bp in blueprints]
    # positive case: suggest token/id extractors when envelope looks like auth/create
    for case in cases:
        if "positive" not in (case.get("tags") or []):
            continue
        if case.get("extractors"):
            continue
        hints: list[dict[str, Any]] = []
        name_l = (definition.name or "").lower()
        path_l = (definition.path or "").lower()
        if "login" in path_l or "token" in path_l or "登录" in (definition.name or ""):
            hints.append({"name": "token", "type": "jsonpath", "expr": "$.data.token"})
        elif "id" in name_l or str(definition.method or "").upper() in {"POST", "PUT"}:
            hints.append({"name": "id", "type": "jsonpath", "expr": "$.data.id"})
        if hints:
            case["extractors"] = hints
        break
    summary = response_assert_summary(sample)
    warnings: list[str] = []
    needed: list[str] = []
    for c in cases:
        for v in c.get("env_vars_needed") or []:
            if v not in needed:
                needed.append(v)
    if needed:
        warnings.append(
            f"{definition.name} 使用变量 {', '.join(needed)}，请在 API 环境中配置，或改用场景 Agent 串联造数"
        )
    return {
        "definition_id": definition.id,
        "definition_name": definition.name,
        "method": definition.method,
        "path": definition.path,
        "module": definition.module or "",
        "description": definition.description or "",
        "cases": cases,
        "warnings": warnings,
        **summary,
    }


def seed_plan_from_groups(groups: list[dict[str, Any]], *, prompt: str = "") -> dict[str, Any]:
    names = [str(g.get("definition_name") or g.get("definition_id")) for g in groups]
    title = "单接口用例草稿"
    if len(names) == 1:
        title = f"{names[0]} - 单接口用例"
    elif names:
        title = f"{names[0]} 等 {len(names)} 个接口 - 单接口用例"
    warnings: list[str] = []
    needs: list[str] = []
    for g in groups:
        for w in g.get("warnings") or []:
            if w not in warnings:
                warnings.append(str(w))
        for c in g.get("cases") or []:
            for v in c.get("env_vars_needed") or []:
                if v not in needs:
                    needs.append(str(v))
    summary = (prompt or "").strip() or f"按单接口设计规则为 {len(groups)} 个接口生成用例矩阵（接口间不串联）"
    return {
        "title": title[:512],
        "summary": summary[:2000],
        "groups": [
            {
                "definition_id": int(g["definition_id"]),
                "definition_name": g.get("definition_name"),
                "method": g.get("method"),
                "path": g.get("path"),
                "cases": list(g.get("cases") or []),
            }
            for g in groups
        ],
        "warnings": warnings,
        "needs_user_input": [f"请确认环境变量已配置：{', '.join(needs)}"] if needs else [],
    }


def _normalize_assertion(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    atype = str(raw.get("type") or "").strip()
    if not atype:
        return None
    out: dict[str, Any] = {"type": atype}
    for k in ("expected", "expr", "op", "value", "name"):
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    return out


def _normalize_extractor(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    expr = str(raw.get("expr") or "").strip()
    if not name or not expr:
        return None
    return {
        "name": name[:64],
        "type": str(raw.get("type") or "jsonpath"),
        "expr": expr,
    }


def _normalize_case(raw: Any, *, method: str, path: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    body = raw.get("body")
    if body is not None and not isinstance(body, str):
        try:
            body = json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            body = str(body)
    assertions = []
    for a in raw.get("assertions") or []:
        na = _normalize_assertion(a)
        if na:
            assertions.append(na)
    if not any(a.get("type") == "status" for a in assertions):
        assertions.insert(0, {"type": "status", "expected": 200})
    extractors = []
    for e in raw.get("extractors") or []:
        ne = _normalize_extractor(e)
        if ne:
            extractors.append(ne)
    tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t or "").strip()]
    query = _as_str_dict(raw.get("query"))
    headers = _as_str_dict(raw.get("headers"))
    case_path = str(raw.get("path") or path or "/")
    case_method = str(raw.get("method") or method or "GET").upper()
    body_text = str(body or "")
    env_vars = [
        str(v).strip()
        for v in (raw.get("env_vars_needed") or [])
        if str(v or "").strip()
    ]
    for v in _extract_env_vars(
        case_path,
        body_text,
        json.dumps(headers, ensure_ascii=False),
        json.dumps(query, ensure_ascii=False),
    ):
        if v not in env_vars:
            env_vars.append(v)
    return {
        "name": name[:512],
        "tags": tags or ["auto-gen"],
        "method": case_method,
        "path": case_path,
        "query": query,
        "headers": headers,
        "body_type": str(raw.get("body_type") or ("json" if body_text.strip().startswith(("{", "[")) else "none")),
        "body": body_text,
        "assertions": assertions,
        "extractors": extractors,
        "timeout_ms": max(1000, min(int(raw.get("timeout_ms") or 15000), 120000)),
        "description": str(raw.get("description") or raw.get("rationale") or "")[:2000],
        "env_vars_needed": env_vars,
        "rationale": str(raw.get("rationale") or "")[:1000],
    }


def normalize_case_plan(
    plan: dict[str, Any] | None,
    *,
    allowed_definition_ids: set[int],
    catalog_by_id: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(plan, dict):
        return None, ["计划为空"]
    raw_groups = plan.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return None, ["计划缺少 groups"]
    groups: list[dict[str, Any]] = []
    errs: list[str] = []
    seen_defs: set[int] = set()
    for raw in raw_groups[:_MAX_GROUPS]:
        if not isinstance(raw, dict):
            continue
        try:
            did = int(raw.get("definition_id"))
        except (TypeError, ValueError):
            errs.append("存在无效 definition_id")
            continue
        if did not in allowed_definition_ids:
            errs.append(f"definition_id={did} 不在候选列表")
            continue
        if did in seen_defs:
            continue
        seen_defs.add(did)
        meta = catalog_by_id.get(did) or {}
        method = str(raw.get("method") or meta.get("method") or "GET").upper()
        path = str(raw.get("path") or meta.get("path") or "/")
        cases: list[dict[str, Any]] = []
        for c in (raw.get("cases") or [])[:_MAX_CASES_PER_DEF]:
            nc = _normalize_case(c, method=method, path=path)
            if nc:
                nc["method"] = method
                raw_path = str(c.get("path") or "").strip()
                nc["path"] = raw_path if "{{" in raw_path else path
                cases.append(nc)
        if not cases:
            errs.append(f"接口 #{did} 没有有效用例")
            continue
        groups.append(
            {
                "definition_id": did,
                "definition_name": str(raw.get("definition_name") or meta.get("name") or f"#{did}"),
                "method": method,
                "path": path,
                "cases": cases,
            }
        )
    if not groups:
        return None, errs or ["没有可落库的用例分组"]
    warnings = [str(w) for w in (plan.get("warnings") or []) if str(w or "").strip()]
    needs = [str(n) for n in (plan.get("needs_user_input") or []) if str(n or "").strip()]
    env_seen: set[str] = set()
    for g in groups:
        for c in g["cases"]:
            for v in c.get("env_vars_needed") or []:
                if v in env_seen:
                    continue
                env_seen.add(v)
                tip = f"请确认环境变量已配置：{v}"
                if tip not in needs:
                    needs.append(tip)
    out = {
        "title": str(plan.get("title") or "单接口用例草稿")[:512],
        "summary": str(plan.get("summary") or "")[:2000],
        "groups": groups,
        "warnings": warnings,
        "needs_user_input": needs[:20],
    }
    return out, errs


def collect_context(state: CaseAgentState) -> dict[str, Any]:
    defs = state.get("candidate_definitions") or []
    _log(state, "collect_context", "开始收集候选接口与规则种子", count=len(defs))
    if not defs:
        _log(state, "collect_context", "候选接口为空", level="error")
        return {"error": "候选接口为空，请先选择接口定义"}
    seeds = [d.get("seed_group") for d in defs if isinstance(d.get("seed_group"), dict)]
    if not seeds:
        return {"error": "未能生成规则种子用例"}
    _log(state, "collect_context", f"已加载 {len(defs)} 个接口，种子分组 {len(seeds)}")
    return {"error": None, "seed_groups": seeds}


def draft_plan(state: CaseAgentState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    _log(state, "draft_plan", "开始生成单接口用例计划")
    seeds = state.get("seed_groups") or []
    fallback = seed_plan_from_groups(seeds, prompt=state.get("prompt") or "")
    cfg = _llm_by_thread.get(str(state.get("thread_id") or ""))
    chat = list(state.get("messages") or [])
    prompt = (state.get("prompt") or "").strip()
    chat.append({"role": "user", "content": prompt or "请根据所选接口生成单接口用例矩阵"})
    if cfg is None:
        chat.append({"role": "assistant", "content": f"已按规则生成计划：{fallback.get('title')}"})
        _log(state, "draft_plan", "LLM 未绑定，使用规则种子", title=str(fallback.get("title") or ""))
        return {"plan": fallback, "error": None, "messages": chat}

    catalog = []
    for d in state.get("candidate_definitions") or []:
        catalog.append(
            {
                "id": d["id"],
                "name": d.get("name"),
                "method": d.get("method"),
                "path": d.get("path"),
                "module": d.get("module"),
                "description": d.get("description") or "",
                "success_status": d.get("success_status", 200),
                "required_fields": d.get("required_fields") or [],
                "envelope_keys": d.get("envelope_keys") or [],
                "envelope_eq": d.get("envelope_eq") or {},
                "seed_case_names": [c.get("name") for c in (d.get("seed_group") or {}).get("cases") or []],
            }
        )
    user_content = (
        f"业务说明（可为空）：\n{prompt or '（未提供，请严格按规则种子扩展/精炼）'}\n\n"
        f"候选接口 JSON：\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"规则种子计划 JSON（优先保留覆盖面，可按说明增删改）：\n"
        f"{json.dumps(fallback, ensure_ascii=False)}\n\n"
        "请输出单接口用例计划 JSON。不要把接口串成场景。"
    )
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_content},
    ]
    try:
        plan = llm_json(messages, cfg=cfg, scene="接口单用例")
    except Exception as e:  # noqa: BLE001
        log.exception("case agent draft_plan failed")
        _log(state, "draft_plan", f"LLM 失败，回退规则种子: {e}", level="warning")
        chat.append({"role": "assistant", "content": f"模型生成失败，已用规则种子：{fallback.get('title')}"})
        return {"plan": fallback, "error": None, "messages": chat}
    chat.append({"role": "assistant", "content": f"已生成计划：{plan.get('title') or '未命名'}"})
    _log(state, "draft_plan", "计划草稿已生成", title=str(plan.get("title") or ""))
    return {"plan": plan, "error": None, "messages": chat}


def revise_plan(state: CaseAgentState) -> dict[str, Any]:
    if state.get("error") and not state.get("plan"):
        return {}
    msg = (state.get("revise_message") or "").strip()
    if not msg:
        return {"error": "修改说明为空", "user_decision": "pending"}
    _log(state, "revise_plan", f"根据用户反馈修改计划: {msg[:120]}")
    cfg = _llm_by_thread.get(str(state.get("thread_id") or ""))
    if cfg is None:
        return {"error": "LLM 未配置"}
    chat = list(state.get("messages") or [])
    chat.append({"role": "user", "content": msg})
    current = state.get("plan") or {}
    catalog = [
        {
            "id": d["id"],
            "name": d.get("name"),
            "method": d.get("method"),
            "path": d.get("path"),
        }
        for d in (state.get("candidate_definitions") or [])
    ]
    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": (
                f"当前计划 JSON：\n{json.dumps(current, ensure_ascii=False)}\n\n"
                f"候选接口 JSON：\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
                f"用户修改要求：\n{msg}\n\n"
                "请输出修订后的完整单接口用例计划 JSON（不要只返回 diff；不要改成场景串联）。"
            ),
        },
    ]
    try:
        plan = llm_json(messages, cfg=cfg, scene="接口单用例")
    except Exception as e:  # noqa: BLE001
        log.exception("case agent revise_plan failed")
        _log(state, "revise_plan", f"修改失败: {e}", level="error")
        return {"error": f"修改计划失败: {e}", "revise_message": None}
    chat.append({"role": "assistant", "content": f"已按你的要求更新计划：{plan.get('title') or '未命名'}"})
    _log(state, "revise_plan", "计划已更新", title=str(plan.get("title") or ""))
    return {
        "plan": plan,
        "error": None,
        "messages": chat,
        "revise_message": None,
        "user_decision": "pending",
    }


def validate_plan(state: CaseAgentState) -> dict[str, Any]:
    if state.get("error") and not state.get("plan"):
        return {}
    _log(state, "validate_plan", "开始校验单接口用例计划")
    allowed = set(int(x) for x in (state.get("candidate_definition_ids") or []))
    catalog_by_id = {int(d["id"]): d for d in (state.get("candidate_definitions") or [])}
    plan, errs = normalize_case_plan(
        state.get("plan"),
        allowed_definition_ids=allowed,
        catalog_by_id=catalog_by_id,
    )
    if plan is None:
        # fall back to seeds if first draft invalid
        seeds = state.get("seed_groups") or []
        if seeds and not state.get("revise_message"):
            plan, errs2 = normalize_case_plan(
                seed_plan_from_groups(seeds, prompt=state.get("prompt") or ""),
                allowed_definition_ids=allowed,
                catalog_by_id=catalog_by_id,
            )
            errs = errs2
        if plan is None:
            msg = "; ".join(errs) if errs else "计划校验失败"
            _log(state, "validate_plan", f"校验失败: {msg}", level="error")
            return {"error": msg}
    total = sum(len(g.get("cases") or []) for g in plan["groups"])
    _log(state, "validate_plan", f"校验通过，共 {len(plan['groups'])} 组 / {total} 条用例")
    return {"plan": plan, "error": None}


def wait_user_confirm(state: CaseAgentState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    tid = str(state.get("thread_id") or "")
    plan = state.get("plan")
    get_event_bus().append(tid, {"type": "plan", "plan": plan})
    get_event_bus().append(
        tid,
        {"type": "interrupted", "message": "可继续对话修改用例草稿，或最终确认写入单接口用例"},
    )
    _log(state, "wait_user_confirm", "已暂停，等待用户确认或继续修改")
    decision = interrupt({"plan": plan, "message": "等待用户确认或继续修改"})
    if not isinstance(decision, dict):
        decision = {"action": "cancel"}
    action = str(decision.get("action") or "cancel")
    revise_message = str(decision.get("message") or "").strip() if action == "revise" else None
    _log(state, "wait_user_confirm", f"用户决定: {action}")
    return {
        "user_decision": action,
        "revise_message": revise_message,
        "error": None,
    }


def materialize_cases(state: CaseAgentState, *, db: Session) -> dict[str, Any]:
    if state.get("error"):
        return {}
    if state.get("user_decision") != "confirm":
        _log(state, "materialize_cases", "用户取消，跳过落库")
        return {"created_case_ids": []}
    plan = state.get("plan") or {}
    groups = plan.get("groups") if isinstance(plan.get("groups"), list) else []
    project_id = int(state["project_id"])
    user_id = int(state["user_id"])
    created_ids: list[int] = []
    _log(state, "materialize_cases", f"开始写入单接口用例，分组 {len(groups)}")
    for g in groups:
        try:
            did = int(g.get("definition_id"))
        except (TypeError, ValueError):
            continue
        definition = (
            db.query(ApiDefinition)
            .filter(ApiDefinition.id == did, ApiDefinition.project_id == project_id)
            .first()
        )
        if definition is None:
            return {"error": f"接口 #{did} 不存在", "created_case_ids": created_ids}
        for raw in g.get("cases") or []:
            if not isinstance(raw, dict):
                continue
            case = ApiCase(
                project_id=project_id,
                api_id=did,
                name=str(raw.get("name") or f"{definition.name} 用例")[:512],
                description=str(raw.get("description") or raw.get("rationale") or ""),
                method=str(raw.get("method") or definition.method or "GET").upper(),
                path=str(raw.get("path") or definition.path or "/"),
                query_json=_as_str_dict(raw.get("query")),
                headers_json=_as_str_dict(raw.get("headers")),
                body_type=str(raw.get("body_type") or "none"),
                body_text=str(raw.get("body") or ""),
                assertions_json=list(raw.get("assertions") or []),
                extractors_json=list(raw.get("extractors") or []),
                timeout_ms=int(raw.get("timeout_ms") or 15000),
                status="draft",
                tags_json=list(raw.get("tags") or ["auto-gen", "ai-case-agent"]),
                creator_id=user_id,
            )
            tags = case.tags_json or []
            if "ai-case-agent" not in tags:
                case.tags_json = [*tags, "ai-case-agent"]
            db.add(case)
            db.flush()
            created_ids.append(case.id)
        _log(
            state,
            "materialize_cases",
            f"接口 #{did} 写入 {len(g.get('cases') or [])} 条",
        )
    db.commit()
    _log(state, "materialize_cases", f"已写入 {len(created_ids)} 条单接口用例")
    return {"created_case_ids": created_ids, "plan": {**plan, "created_case_ids": created_ids}}


def finalize(state: CaseAgentState) -> dict[str, Any]:
    tid = str(state.get("thread_id") or "")
    if state.get("error") and state.get("user_decision") not in ("confirm", "cancel", "revise"):
        get_event_bus().append(tid, {"type": "error", "message": state.get("error")})
        get_event_bus().append(
            tid,
            {"type": "done", "status": "error", "created_case_ids": [], "run_id": None},
        )
        _log(state, "finalize", f"结束(错误): {state.get('error')}", level="error")
        return {}
    decision = state.get("user_decision")
    if decision == "cancel":
        status = "cancelled"
    elif decision == "confirm":
        status = "confirmed"
    else:
        return {}
    get_event_bus().append(
        tid,
        {
            "type": "done",
            "status": status,
            "created_case_ids": state.get("created_case_ids") or [],
            "run_id": state.get("run_id"),
        },
    )
    _log(state, "finalize", f"结束 status={status}")
    return {}


def _route_after_validate(state: CaseAgentState) -> Literal["wait_user_confirm", "finalize"]:
    if state.get("error"):
        return "finalize"
    return "wait_user_confirm"


def _route_after_confirm(
    state: CaseAgentState,
) -> Literal["materialize_cases", "revise_plan", "finalize"]:
    decision = state.get("user_decision")
    if decision == "confirm":
        return "materialize_cases"
    if decision == "revise":
        return "revise_plan"
    return "finalize"


def build_graph(*, db_factory):
    def _materialize(state: CaseAgentState) -> dict[str, Any]:
        db = db_factory()
        try:
            return materialize_cases(state, db=db)
        finally:
            db.close()

    g = StateGraph(CaseAgentState)
    g.add_node("collect_context", collect_context)
    g.add_node("draft_plan", draft_plan)
    g.add_node("revise_plan", revise_plan)
    g.add_node("validate_plan", validate_plan)
    g.add_node("wait_user_confirm", wait_user_confirm)
    g.add_node("materialize_cases", _materialize)
    g.add_node("finalize", finalize)

    g.add_edge(START, "collect_context")
    g.add_edge("collect_context", "draft_plan")
    g.add_edge("draft_plan", "validate_plan")
    g.add_edge("revise_plan", "validate_plan")
    g.add_conditional_edges("validate_plan", _route_after_validate)
    g.add_conditional_edges("wait_user_confirm", _route_after_confirm)
    g.add_edge("materialize_cases", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=_checkpointer)


def get_compiled_graph(*, db_factory):
    global _compiled
    if _compiled is None:
        _compiled = build_graph(db_factory=db_factory)
    return _compiled


def reset_compiled_graph() -> None:
    global _compiled
    _compiled = None


def load_candidate_definitions(
    db: Session, project_id: int, definition_ids: list[int]
) -> list[dict[str, Any]]:
    rows = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.project_id == project_id, ApiDefinition.id.in_(definition_ids))
        .all()
    )
    by_id = {r.id: r for r in rows}
    out: list[dict[str, Any]] = []
    for did in definition_ids:
        r = by_id.get(did)
        if r is None:
            continue
        seed = _seed_group_from_definition(db, r)
        sample = _sample_context(db, r)
        summary = response_assert_summary(sample)
        out.append(
            {
                "id": r.id,
                "name": r.name,
                "method": r.method,
                "path": r.path,
                "module": r.module or "",
                "description": r.description or "",
                "seed_group": seed,
                **summary,
            }
        )
    return out


def new_thread_id() -> str:
    return uuid.uuid4().hex


def persist_session_events(db: Session, session: ApiCaseAgentSession) -> None:
    events = get_event_bus().snapshot(session.thread_id)
    session.events_json = events
    db.add(session)
    db.commit()


def sync_session_from_state(
    db: Session,
    session: ApiCaseAgentSession,
    *,
    plan: dict | None,
    created_case_ids: list[int] | None,
    run_id: int | None,
    status: str,
    error: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> None:
    session.plan_json = plan
    session.created_case_ids_json = created_case_ids
    session.run_id = run_id
    session.status = status
    if messages is not None:
        session.messages_json = messages
    if error:
        session.error_message = error
    elif status != "error":
        session.error_message = ""
    persist_session_events(db, session)


def run_until_interrupt(
    *,
    db_factory,
    thread_id: str,
    project_id: int,
    user_id: int,
    prompt: str,
    candidate_definitions: list[dict[str, Any]],
    llm_cfg: LlmRuntime,
) -> dict[str, Any]:
    bind_llm(thread_id, llm_cfg)
    graph = get_compiled_graph(db_factory=db_factory)
    config = {"configurable": {"thread_id": thread_id}}
    init: CaseAgentState = {
        "project_id": project_id,
        "user_id": user_id,
        "prompt": prompt or "",
        "candidate_definitions": candidate_definitions,
        "candidate_definition_ids": [int(d["id"]) for d in candidate_definitions],
        "seed_groups": [],
        "messages": [],
        "plan": None,
        "user_decision": "pending",
        "revise_message": None,
        "created_case_ids": None,
        "run_id": None,
        "error": None,
        "thread_id": thread_id,
    }
    result = graph.invoke(init, config)
    return result if isinstance(result, dict) else {}


def resume_after_confirm(
    *,
    db_factory,
    thread_id: str,
    action: str,
    message: str | None = None,
    unbind: bool = True,
) -> dict[str, Any]:
    graph = get_compiled_graph(db_factory=db_factory)
    config = {"configurable": {"thread_id": thread_id}}
    payload: dict[str, Any] = {"action": action}
    if message is not None:
        payload["message"] = message
    try:
        result = graph.invoke(Command(resume=payload), config)
        return result if isinstance(result, dict) else {}
    finally:
        if unbind and action in ("confirm", "cancel"):
            unbind_llm(thread_id)
