"""LangGraph: plan-first API scenario agent (definitions + multi-turn revise)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session

from app.models.api_automation.api_automation import (
    ApiDefinition,
    ApiScenarioAgentSession,
    ApiSuite,
)
from app.services.api_automation import api_definitions as def_svc
from app.services.api_automation.api_assertions import (
    build_scenario_assertions,
    resolve_extract_expr,
    response_assert_summary,
    sample_from_catalog_summary,
)
from app.services.api_automation.api_case_gen import _sample_context
from app.services.api_automation.api_scenario_events import emit_log, get_event_bus
from app.services.api_automation.api_scenario_llm import llm_json
from app.services.api_automation.api_suite_steps import (
    apply_consume_hints_to_payload,
    normalize_definition_steps,
    normalize_steps,
    validate_steps_graph,
)
from app.services.ai.effective_ai_settings import LlmRuntime

log = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_compiled = None
_llm_by_thread: dict[str, LlmRuntime] = {}


class ScenarioAgentState(TypedDict, total=False):
    project_id: int
    user_id: int
    prompt: str
    candidate_definitions: list[dict[str, Any]]
    candidate_definition_ids: list[int]
    messages: list[dict[str, str]]  # chat turns {role, content}
    plan: dict[str, Any] | None
    user_decision: str | None  # pending | confirm | cancel | revise
    revise_message: str | None
    suite_id: int | None
    run_id: int | None
    error: str | None
    thread_id: str


def bind_llm(thread_id: str, cfg: LlmRuntime) -> None:
    _llm_by_thread[thread_id] = cfg


def unbind_llm(thread_id: str) -> None:
    _llm_by_thread.pop(thread_id, None)


def _system_prompt() -> str:
    return (
        "你是接口自动化场景编排助手。根据候选「接口定义」列表与可选业务描述，"
        "设计一条按接口串联的场景计划，并明确步骤间变量传参与响应断言。"
        "只输出一个 JSON 对象，不要 Markdown。格式："
        '{"title":"...","summary":"...","steps":['
        '{"id":"s1","definition_id":101,"definition_name":"...","method":"POST","path":"/x",'
        '"on_pass":"s2","on_fail":"end","when":null,'
        '"extract_hints":["id"],'
        '"consume_hints":[{"var":"id","from_step":"s1","in":"path","key":"id","template":"{{id}}"}],'
        '"assertions":[{"type":"status","expected":201},'
        '{"type":"jsonpath","expr":"$.id","op":"exists"}],'
        '"rationale":"..."}'
        '],"warnings":[],"needs_user_input":[]}'
        "约束：steps 里的 definition_id 必须来自候选接口；id 唯一；on_pass/on_fail 为其它步骤 id 或 end；"
        "when 可为 null 或 {\"op\":\"eq|ne|contains|exists\",\"left\":\"{{var}}\",\"right\":\"...\"}；"
        "extract_hints：本步响应要提取的变量名，优先用 required_fields / response_keys 中的字段名"
        "（如 id、token），不要臆造业务别名（如 caseId）；无需提取则 []；"
        "consume_hints：本步请求要用的上游变量；in 为 header|query|path|body；"
        "key 为头名/参数名/路径占位名/body 字段名；template 默认 {{var}}，鉴权常用 Bearer {{token}}；"
        "from_step 为产出该变量的步骤 id；无传参则 consume_hints 为 []；"
        "assertions：利用候选接口的 success_status / required_fields / envelope_keys / envelope_eq / response_keys；"
        "type 仅用 status、jsonpath、contains、not_contains、regex、header；"
        "jsonpath 的 op 为 eq|exists|contains|regex；优先 status + 字段 exists + 信封 eq（code/success）；"
        "jsonpath 必须对齐 schema：envelope_keys 不含 data 时禁止写 $.data.*；"
        "扁平响应用 $.id 等顶层路径；仅当 envelope_keys 含 data 时才用 $.data.x；"
        "禁止编造随机 expected（不要用示例里的随机数字/字符串当等值期望）；无把握用 exists；"
        "若无业务描述，按接口 method/path/name 推断合理顺序（如登录鉴权优先），并补齐传参与断言。"
    )


def _log(state: ScenarioAgentState, node: str, message: str, level: str = "info", **data: Any) -> None:
    tid = str(state.get("thread_id") or "")
    if not tid:
        return
    emit_log(tid, node=node, message=message, level=level, data=data or None)


def _catalog(state: ScenarioAgentState) -> list[dict[str, Any]]:
    defs = state.get("candidate_definitions") or []
    out: list[dict[str, Any]] = []
    for d in defs:
        out.append(
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
                "response_keys": d.get("response_keys") or [],
            }
        )
    return out


def collect_context(state: ScenarioAgentState) -> dict[str, Any]:
    defs = state.get("candidate_definitions") or []
    _log(state, "collect_context", "开始收集候选接口上下文", count=len(defs))
    if not defs:
        _log(state, "collect_context", "候选接口为空", level="error")
        return {"error": "候选接口为空，请先选择接口定义"}
    _log(state, "collect_context", f"已加载 {len(defs)} 个候选接口")
    return {"error": None}


def draft_plan(state: ScenarioAgentState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    _log(state, "draft_plan", "开始生成场景计划（按接口串联）")
    cfg = _llm_by_thread.get(str(state.get("thread_id") or ""))
    if cfg is None:
        return {"error": "LLM 未配置"}
    prompt = (state.get("prompt") or "").strip()
    user_content = (
        f"业务描述（可为空）：\n{prompt or '（未提供，请按候选接口推断）'}\n\n"
        f"候选接口 JSON：\n{json.dumps(_catalog(state), ensure_ascii=False)}\n\n"
        "请输出场景计划 JSON。"
    )
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_content},
    ]
    chat = list(state.get("messages") or [])
    chat.append({"role": "user", "content": prompt or "请根据所选接口生成场景串联计划"})
    try:
        plan = llm_json(messages, cfg=cfg, scene="接口场景")
    except Exception as e:  # noqa: BLE001
        log.exception("draft_plan failed")
        _log(state, "draft_plan", f"生成失败: {e}", level="error")
        return {"error": f"生成计划失败: {e}", "plan": None, "messages": chat}
    chat.append({"role": "assistant", "content": f"已生成计划：{plan.get('title') or '未命名'}"})
    _log(state, "draft_plan", "计划草稿已生成", title=str(plan.get("title") or ""))
    return {"plan": plan, "error": None, "messages": chat}


def revise_plan(state: ScenarioAgentState) -> dict[str, Any]:
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
    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": (
                f"当前计划 JSON：\n{json.dumps(current, ensure_ascii=False)}\n\n"
                f"候选接口 JSON：\n{json.dumps(_catalog(state), ensure_ascii=False)}\n\n"
                f"用户修改要求：\n{msg}\n\n"
                "请输出修订后的完整场景计划 JSON（不要只返回 diff）。"
            ),
        },
    ]
    try:
        plan = llm_json(messages, cfg=cfg, scene="接口场景")
    except Exception as e:  # noqa: BLE001
        log.exception("revise_plan failed")
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


def validate_plan(state: ScenarioAgentState) -> dict[str, Any]:
    if state.get("error") and not state.get("plan"):
        return {}
    # clear soft error from revise if we have a plan to re-validate
    _log(state, "validate_plan", "开始校验计划（definition_id）")
    plan = state.get("plan") or {}
    raw_steps = plan.get("steps") if isinstance(plan, dict) else None
    steps = normalize_definition_steps(raw_steps if isinstance(raw_steps, list) else None)
    allowed = set(int(x) for x in (state.get("candidate_definition_ids") or []))
    errs = validate_steps_graph(steps, allowed_definition_ids=allowed, id_field="definition_id")
    if errs:
        msg = "; ".join(errs)
        _log(state, "validate_plan", f"校验失败: {msg}", level="error")
        return {"error": msg}
    name_by_id = {int(d["id"]): d for d in (state.get("candidate_definitions") or [])}
    for s in steps:
        d = name_by_id.get(int(s["definition_id"]))
        if d:
            s.setdefault("definition_name", d.get("name"))
            s.setdefault("method", d.get("method"))
            s.setdefault("path", d.get("path"))
        hints = s.get("extract_hints") if isinstance(s.get("extract_hints"), list) else []
        s["assertions"] = build_scenario_assertions(
            sample_from_catalog_summary(d),
            extract_hints=hints,
            llm_assertions=s.get("assertions"),
        )
    plan = dict(plan)
    plan["steps"] = steps
    plan.setdefault("title", "未命名场景")
    plan.setdefault("summary", "")
    plan.setdefault("warnings", [])
    _log(state, "validate_plan", f"校验通过，共 {len(steps)} 步接口")
    return {"plan": plan, "error": None}


def wait_user_confirm(state: ScenarioAgentState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    tid = str(state.get("thread_id") or "")
    plan = state.get("plan")
    get_event_bus().append(tid, {"type": "plan", "plan": plan})
    get_event_bus().append(
        tid,
        {"type": "interrupted", "message": "可继续对话修改计划，或最终确认生成场景"},
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


def materialize_suite(state: ScenarioAgentState, *, db: Session) -> dict[str, Any]:
    if state.get("error"):
        return {}
    if state.get("user_decision") != "confirm":
        _log(state, "materialize_suite", "用户取消，跳过落库")
        return {"suite_id": None}
    _log(state, "materialize_suite", "开始按接口写入场景步骤（内嵌请求，不绑定用例）")
    plan = state.get("plan") or {}
    def_steps = normalize_definition_steps(plan.get("steps") if isinstance(plan.get("steps"), list) else None)
    project_id = int(state["project_id"])
    user_id = int(state["user_id"])
    runtime_steps: list[dict[str, Any]] = []

    for s in def_steps:
        def_id = int(s["definition_id"])
        definition = (
            db.query(ApiDefinition)
            .filter(ApiDefinition.id == def_id, ApiDefinition.project_id == project_id)
            .first()
        )
        if definition is None:
            return {"error": f"接口 #{def_id} 不存在", "suite_id": None}

        payload = def_svc.definition_to_step_payload(definition)
        consume = s.get("consume_hints") if isinstance(s.get("consume_hints"), list) else []
        payload = apply_consume_hints_to_payload(payload, consume)

        hints = s.get("extract_hints") if isinstance(s.get("extract_hints"), list) else []
        sample_ctx = _sample_context(db, definition)
        extractors: list[dict[str, Any]] = []
        for h in hints:
            name = str(h or "").strip()
            if not name:
                continue
            extractors.append(
                {"name": name, "type": "jsonpath", "expr": resolve_extract_expr(sample_ctx, name)}
            )
        if extractors:
            payload["extractors"] = extractors
        payload["assertions"] = build_scenario_assertions(
            sample_ctx,
            extract_hints=hints,
            llm_assertions=s.get("assertions"),
        )

        step_row: dict[str, Any] = {
            "id": s["id"],
            **payload,
            "definition_name": s.get("definition_name") or definition.name,
            "when": s.get("when"),
            "on_pass": s.get("on_pass") or "end",
            "on_fail": s.get("on_fail") or "end",
            "rationale": s.get("rationale"),
        }
        # keep plan-level data-flow hints for audit / UI after confirm
        if hints:
            step_row["extract_hints"] = [str(x) for x in hints if str(x or "").strip()]
        if consume:
            step_row["consume_hints"] = consume
        runtime_steps.append(step_row)
        _log(
            state,
            "materialize_suite",
            f"步骤 {s['id']} ← 接口 #{def_id} {definition.method} {definition.path}"
            f" 产出={hints or []} 消费={len(consume)}",
        )

    steps = normalize_steps(runtime_steps, None)
    by_id = {str(x["id"]): x for x in runtime_steps}
    for st in steps:
        extra = by_id.get(str(st["id"]))
        if not extra:
            continue
        if extra.get("extract_hints"):
            st["extract_hints"] = extra["extract_hints"]
        if extra.get("consume_hints"):
            st["consume_hints"] = extra["consume_hints"]
    title = str(plan.get("title") or "AI 接口场景")[:512]
    summary = str(plan.get("summary") or "")
    row = ApiSuite(
        project_id=project_id,
        name=title,
        description=summary,
        case_ids_json=[],
        steps_json=steps,
        stop_on_fail=True,
        env_id=None,
        creator_id=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _log(state, "materialize_suite", f"已写入场景 #{row.id}（{len(steps)} 步接口链路）")
    return {"suite_id": row.id, "plan": {**plan, "steps": steps}}


def finalize(state: ScenarioAgentState) -> dict[str, Any]:
    tid = str(state.get("thread_id") or "")
    if state.get("error") and state.get("user_decision") not in ("confirm", "cancel", "revise"):
        get_event_bus().append(tid, {"type": "error", "message": state.get("error")})
        get_event_bus().append(tid, {"type": "done", "status": "error", "suite_id": None, "run_id": None})
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
            "suite_id": state.get("suite_id"),
            "run_id": state.get("run_id"),
        },
    )
    _log(state, "finalize", f"结束 status={status}")
    return {}


def _route_after_validate(state: ScenarioAgentState) -> Literal["wait_user_confirm", "finalize"]:
    if state.get("error"):
        return "finalize"
    return "wait_user_confirm"


def _route_after_confirm(
    state: ScenarioAgentState,
) -> Literal["materialize_suite", "revise_plan", "finalize"]:
    decision = state.get("user_decision")
    if decision == "confirm":
        return "materialize_suite"
    if decision == "revise":
        return "revise_plan"
    return "finalize"


def build_graph(*, db_factory):
    def _materialize(state: ScenarioAgentState) -> dict[str, Any]:
        db = db_factory()
        try:
            return materialize_suite(state, db=db)
        finally:
            db.close()

    g = StateGraph(ScenarioAgentState)
    g.add_node("collect_context", collect_context)
    g.add_node("draft_plan", draft_plan)
    g.add_node("revise_plan", revise_plan)
    g.add_node("validate_plan", validate_plan)
    g.add_node("wait_user_confirm", wait_user_confirm)
    g.add_node("materialize_suite", _materialize)
    g.add_node("finalize", finalize)

    g.add_edge(START, "collect_context")
    g.add_edge("collect_context", "draft_plan")
    g.add_edge("draft_plan", "validate_plan")
    g.add_edge("revise_plan", "validate_plan")
    g.add_conditional_edges("validate_plan", _route_after_validate)
    g.add_conditional_edges("wait_user_confirm", _route_after_confirm)
    g.add_edge("materialize_suite", "finalize")
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
                **summary,
            }
        )
    return out


def new_thread_id() -> str:
    return uuid.uuid4().hex


def persist_session_events(db: Session, session: ApiScenarioAgentSession) -> None:
    events = get_event_bus().snapshot(session.thread_id)
    session.events_json = events
    db.add(session)
    db.commit()


def sync_session_from_state(
    db: Session,
    session: ApiScenarioAgentSession,
    *,
    plan: dict | None,
    suite_id: int | None,
    run_id: int | None,
    status: str,
    error: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> None:
    session.plan_json = plan
    session.suite_id = suite_id
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
    init: ScenarioAgentState = {
        "project_id": project_id,
        "user_id": user_id,
        "prompt": prompt or "",
        "candidate_definitions": candidate_definitions,
        "candidate_definition_ids": [int(d["id"]) for d in candidate_definitions],
        "messages": [],
        "plan": None,
        "user_decision": "pending",
        "revise_message": None,
        "suite_id": None,
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
