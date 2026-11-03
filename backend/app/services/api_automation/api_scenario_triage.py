"""LangGraph triage: analyze failed step → plan patch → human confirm → optional apply."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.api_automation.api_automation import ApiCase, ApiRun, ApiRunStep, ApiSuite
from app.services.api_automation.api_scenario_events import emit_log, get_event_bus
from app.services.api_automation.api_scenario_llm import llm_json
from app.services.api_automation.api_suite_steps import merge_assertions, merge_extractors, step_has_inline_request
from app.services.ai.effective_ai_settings import LlmRuntime

log = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_compiled = None
_llm_by_thread: dict[str, LlmRuntime] = {}


class TriageState(TypedDict, total=False):
    project_id: int
    run_id: int
    step_id: int
    case_id: int | None
    step_snapshot: dict[str, Any]
    plan: dict[str, Any] | None
    user_decision: str | None
    apply_patch: bool
    patch_message: str | None
    error: str | None
    thread_id: str


def bind_llm(thread_id: str, cfg: LlmRuntime) -> None:
    _llm_by_thread[thread_id] = cfg


def unbind_llm(thread_id: str) -> None:
    _llm_by_thread.pop(thread_id, None)


def _log(state: TriageState, node: str, message: str, level: str = "info", **data: Any) -> None:
    tid = str(state.get("thread_id") or "")
    if tid:
        emit_log(tid, node=node, message=message, level=level, data=data or None)


def analyze(state: TriageState) -> dict[str, Any]:
    _log(state, "analyze", "开始分析失败步骤")
    cfg = _llm_by_thread.get(str(state.get("thread_id") or ""))
    if cfg is None:
        return {"error": "LLM 未配置"}
    snap = state.get("step_snapshot") or {}
    messages = [
        {
            "role": "system",
            "content": (
                "你是接口测试失败归因助手。根据步骤请求/响应/断言结果，输出 JSON："
                '{"summary":"...","likely_causes":["..."],'
                '"suggested_patches":[{"target":"assertions|extractors|headers|body","patch":{...},"rationale":"..."}],'
                '"warnings":[]}'
                "patch 二选一，且必须能直接落库："
                "1) 路径替换：assertions 用 {\"$.旧表达式\":\"$.新表达式\"}；extractors 用 {\"变量名\":\"$.新表达式\"}；"
                "2) 整体替换：{\"assertions\":[{\"type\":\"jsonpath\",\"expr\":\"$.id\",\"op\":\"exists\"}]} "
                "或 {\"extractors\":[{\"name\":\"case_id\",\"type\":\"jsonpath\",\"expr\":\"$.id\"}]}。"
                "只改上下文 executed_assertions / executed_extractors 里真实存在的项。"
                "不要输出 Markdown。敏感信息用 *** 代替。不要编造未提供的响应字段。"
            ),
        },
        {
            "role": "user",
            "content": f"失败步骤上下文：\n{json.dumps(snap, ensure_ascii=False)[:12000]}",
        },
    ]
    try:
        plan = llm_json(messages, cfg=cfg, scene="接口失败归因")
    except Exception as e:  # noqa: BLE001
        log.exception("triage analyze failed")
        _log(state, "analyze", f"分析失败: {e}", level="error")
        return {"error": str(e), "plan": None}
    _log(state, "analyze", "归因计划已生成")
    return {"plan": plan, "error": None}


def wait_confirm(state: TriageState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    tid = str(state.get("thread_id") or "")
    plan = state.get("plan")
    get_event_bus().append(tid, {"type": "plan", "plan": plan})
    get_event_bus().append(tid, {"type": "interrupted", "message": "等待用户确认是否应用修补建议"})
    _log(state, "wait_confirm", "已暂停，等待用户确认")
    decision = interrupt({"plan": plan})
    if not isinstance(decision, dict):
        decision = {"action": "cancel"}
    action = str(decision.get("action") or "cancel")
    apply_patch = bool(decision.get("apply_patch"))
    _log(state, "wait_confirm", f"用户决定: {action}", apply_patch=apply_patch)
    return {"user_decision": action, "apply_patch": apply_patch}


def _string_map(patch: dict[str, Any]) -> dict[str, str]:
    skip = {"assertions", "extractors", "headers", "body"}
    out: dict[str, str] = {}
    for key, value in patch.items():
        if key in skip or not isinstance(value, str):
            continue
        name = str(key).strip()
        if name:
            out[name] = value.strip()
    return out


def _replace_or_rewrite(kind: str, current: list[Any], patch: Any) -> tuple[list[Any], int]:
    """Apply a full list, or a path/name map like {\"$.data.case_id\": \"$.id\"}."""
    if isinstance(patch, list):
        return copy.deepcopy(patch), 1
    if not isinstance(patch, dict):
        return current, 0
    full = patch.get(kind)
    if isinstance(full, list):
        return copy.deepcopy(full), 1
    if isinstance(full, dict):
        patch = full
    mapping = _string_map(patch)
    if not mapping:
        return current, 0
    changed = 0
    out: list[Any] = []
    for item in current:
        if not isinstance(item, dict):
            out.append(item)
            continue
        row = dict(item)
        if kind == "extractors":
            name = str(row.get("name") or "").strip()
            expr = str(row.get("expr") or "").strip()
            if name and name in mapping:
                row["expr"] = mapping[name]
                changed += 1
            elif expr and expr in mapping:
                row["expr"] = mapping[expr]
                changed += 1
        else:
            expr = str(row.get("expr") or "").strip()
            if expr and expr in mapping:
                row["expr"] = mapping[expr]
                changed += 1
        out.append(row)
    return out, changed


def _apply_kind(kind: str, current: Any, patches: list[dict[str, Any]]) -> tuple[list[Any], int]:
    rows = copy.deepcopy(current) if isinstance(current, list) else []
    changed = 0
    for item in patches:
        if str(item.get("target") or "") != kind:
            continue
        rows, n = _replace_or_rewrite(kind, rows, item.get("patch"))
        changed += n
    return rows, changed


def _apply_headers(current: Any, patches: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    headers = dict(current) if isinstance(current, dict) else {}
    changed = 0
    for item in patches:
        if str(item.get("target") or "") != "headers":
            continue
        patch = item.get("patch")
        if not isinstance(patch, dict):
            continue
        if isinstance(patch.get("headers"), dict):
            headers = dict(patch["headers"])
            changed += 1
            continue
        mapping = _string_map(patch)
        if mapping:
            headers.update(mapping)
            changed += 1
    return headers, changed


def _apply_body(current: Any, patches: list[dict[str, Any]]) -> tuple[str | None, int]:
    body = None if current is None else str(current)
    changed = 0
    for item in patches:
        if str(item.get("target") or "") != "body":
            continue
        patch = item.get("patch")
        if isinstance(patch, dict) and "body" in patch:
            body = str(patch["body"])
            changed += 1
        elif isinstance(patch, str):
            body = patch
            changed += 1
    return body, changed


def _suite_node(db: Session, *, project_id: int, run_id: int, step_key: str | None) -> tuple[ApiSuite | None, dict[str, Any] | None]:
    run = db.get(ApiRun, int(run_id))
    if run is None or run.project_id != project_id or run.trigger_type != "suite":
        return None, None
    suite = db.get(ApiSuite, int(run.target_id))
    if suite is None or suite.project_id != project_id or not isinstance(suite.steps_json, list):
        return suite, None
    key = str(step_key or "")
    for node in suite.steps_json:
        if isinstance(node, dict) and str(node.get("id") or "") == key:
            return suite, node
    return suite, None


def _executed_config(db: Session, step: ApiRunStep) -> dict[str, Any]:
    case_assertions: list[Any] = []
    case_extractors: list[Any] = []
    if step.case_id:
        case = db.get(ApiCase, int(step.case_id))
        if case is not None and case.project_id == step.project_id:
            if isinstance(case.assertions_json, list):
                case_assertions = case.assertions_json
            if isinstance(case.extractors_json, list):
                case_extractors = case.extractors_json
    suite, node = _suite_node(db, project_id=step.project_id, run_id=step.run_id, step_key=step.step_key)
    assert_overrides = node.get("assert_overrides") if isinstance(node, dict) else None
    extract_overrides = node.get("extract_overrides") if isinstance(node, dict) else None
    if isinstance(node, dict) and step_has_inline_request(node):
        assertions = node.get("assertions") if isinstance(node.get("assertions"), list) else []
        extractors = node.get("extractors") if isinstance(node.get("extractors"), list) else []
        source = "suite_step"
    else:
        assertions = case_assertions
        extractors = case_extractors
        source = "case" if step.case_id else "unknown"
    return {
        "config_source": source,
        "suite_id": suite.id if suite is not None else None,
        "step_key": step.step_key,
        "executed_assertions": merge_assertions(
            assertions, assert_overrides if isinstance(assert_overrides, list) else None
        ),
        "executed_extractors": merge_extractors(
            extractors, extract_overrides if isinstance(extract_overrides, list) else None
        ),
    }


def apply_patch_node(state: TriageState, *, db: Session) -> dict[str, Any]:
    if state.get("user_decision") != "confirm" or not state.get("apply_patch"):
        _log(state, "apply_patch", "未应用修补")
        return {"patch_message": "未应用修补"}
    plan = state.get("plan") or {}
    patches = [p for p in (plan.get("suggested_patches") or []) if isinstance(p, dict)] if isinstance(plan, dict) else []
    if not patches:
        msg = "未写入：归因计划里没有可应用的修补"
        _log(state, "apply_patch", msg, level="warn")
        return {"patch_message": msg}

    project_id = int(state["project_id"])
    step = db.get(ApiRunStep, int(state["step_id"])) if state.get("step_id") else None
    if step is None or step.project_id != project_id or step.run_id != int(state["run_id"]):
        msg = "未写入：运行步骤不存在"
        _log(state, "apply_patch", msg, level="warn")
        return {"error": msg, "patch_message": msg}

    notes: list[str] = []
    case_missing = False
    case_id = step.case_id or state.get("case_id")
    if case_id:
        case = db.get(ApiCase, int(case_id))
        if case is None or case.project_id != project_id:
            case_missing = True
        else:
            assertions, n_assert = _apply_kind("assertions", case.assertions_json, patches)
            extractors, n_extract = _apply_kind("extractors", case.extractors_json, patches)
            headers, n_headers = _apply_headers(case.headers_json, patches)
            body, n_body = _apply_body(case.body_text, patches)
            case_changed = n_assert + n_extract + n_headers + n_body
            if case_changed:
                bits: list[str] = []
                if n_assert:
                    case.assertions_json = assertions
                    flag_modified(case, "assertions_json")
                    bits.append(f"断言 {n_assert} 处")
                if n_extract:
                    case.extractors_json = extractors
                    flag_modified(case, "extractors_json")
                    bits.append(f"提取 {n_extract} 处")
                if n_headers:
                    case.headers_json = headers
                    flag_modified(case, "headers_json")
                    bits.append("请求头")
                if n_body and body is not None:
                    case.body_text = body
                    bits.append("请求体")
                db.add(case)
                notes.append(f"用例 #{case.id}（{'，'.join(bits)}）")

    suite, node = _suite_node(db, project_id=project_id, run_id=step.run_id, step_key=step.step_key)
    if suite is not None and isinstance(suite.steps_json, list) and node is not None:
        steps = copy.deepcopy(suite.steps_json)
        target = next((s for s in steps if isinstance(s, dict) and str(s.get("id") or "") == str(step.step_key or "")), None)
        suite_bits: list[str] = []
        field_label = {
            "assertions": "断言",
            "assert_overrides": "断言覆盖",
            "extractors": "提取",
            "extract_overrides": "提取覆盖",
        }
        if isinstance(target, dict):
            for field, kind in (
                ("assertions", "assertions"),
                ("assert_overrides", "assertions"),
                ("extractors", "extractors"),
                ("extract_overrides", "extractors"),
            ):
                if not isinstance(target.get(field), list):
                    continue
                updated, n = _apply_kind(kind, target.get(field), patches)
                if n:
                    target[field] = updated
                    suite_bits.append(f"{field_label[field]} {n} 处")
            headers, n_headers = _apply_headers(target.get("headers"), patches)
            if n_headers and isinstance(target.get("headers"), dict):
                target["headers"] = {str(k): str(v) for k, v in headers.items()}
                suite_bits.append("请求头")
            if "body" in target or any(str(p.get("target") or "") == "body" for p in patches):
                body, n_body = _apply_body(target.get("body"), patches)
                if n_body and body is not None:
                    target["body"] = body
                    suite_bits.append("请求体")
        if suite_bits:
            suite.steps_json = steps
            flag_modified(suite, "steps_json")
            db.add(suite)
            notes.append(f"场景 #{suite.id} 步骤 {step.step_key}（{'，'.join(suite_bits)}）")

    if not notes:
        if case_missing:
            msg = "未写入：用例不存在，场景步骤里也没有命中的断言或提取"
        else:
            msg = "未写入：修补没有命中该步骤上的断言、提取或请求。请核对路径是否和当前步骤一致。"
        _log(state, "apply_patch", msg, level="warn")
        return {"patch_message": msg}

    db.commit()
    msg = "已写入 " + "；".join(notes)
    _log(state, "apply_patch", msg)
    return {"patch_message": msg}


def finalize(state: TriageState) -> dict[str, Any]:
    tid = str(state.get("thread_id") or "")
    if state.get("error"):
        get_event_bus().append(tid, {"type": "error", "message": state.get("error")})
        get_event_bus().append(tid, {"type": "done", "status": "error"})
        return {}
    decision = state.get("user_decision")
    status = "cancelled" if decision == "cancel" else "confirmed"
    extra: dict[str, Any] = {}
    if not state.get("error") and decision == "confirm" and not state.get("apply_patch") and not state.get("patch_message"):
        msg = "已确认分析，未修改用例或场景。勾选「确认时写入修补」后再点确认，才会把建议写回去。"
        extra["patch_message"] = msg
        _log(state, "finalize", msg)
    if decision in ("confirm", "cancel"):
        get_event_bus().append(tid, {"type": "done", "status": status})
        _log(state, "finalize", f"结束 status={status}")
    return extra


def _route_after_analyze(state: TriageState) -> Literal["wait_confirm", "finalize"]:
    return "finalize" if state.get("error") else "wait_confirm"


def _route_after_confirm(state: TriageState) -> Literal["apply_patch", "finalize"]:
    if state.get("user_decision") == "confirm" and state.get("apply_patch"):
        return "apply_patch"
    return "finalize"


def build_triage_graph(*, db_factory):
    def _apply(state: TriageState) -> dict[str, Any]:
        db = db_factory()
        try:
            return apply_patch_node(state, db=db)
        finally:
            db.close()

    g = StateGraph(TriageState)
    g.add_node("analyze", analyze)
    g.add_node("wait_confirm", wait_confirm)
    g.add_node("apply_patch", _apply)
    g.add_node("finalize", finalize)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", _route_after_analyze)
    g.add_conditional_edges("wait_confirm", _route_after_confirm)
    g.add_edge("apply_patch", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=_checkpointer)


def get_triage_graph(*, db_factory):
    global _compiled
    if _compiled is None:
        _compiled = build_triage_graph(db_factory=db_factory)
    return _compiled


def reset_triage_graph() -> None:
    global _compiled
    _compiled = None


def new_triage_thread_id() -> str:
    return "triage-" + uuid.uuid4().hex


def step_snapshot(step: ApiRunStep, *, db: Session | None = None) -> dict[str, Any]:
    body = (step.response_body_text or "")[:4000]
    snap: dict[str, Any] = {
        "step_id": step.id,
        "case_id": step.case_id,
        "case_name": step.case_name,
        "step_key": step.step_key,
        "status": step.status,
        "response_status": step.response_status,
        "error_message": (step.error_message or "")[:1000],
        "request_snapshot": step.request_snapshot_json,
        "assertion_results": step.assertion_results_json,
        "extract_results": step.extract_results_json,
        "response_body_preview": body,
    }
    if db is not None:
        snap.update(_executed_config(db, step))
    return snap


def run_triage_until_interrupt(
    *,
    db_factory,
    thread_id: str,
    project_id: int,
    run_id: int,
    step: ApiRunStep,
    llm_cfg: LlmRuntime,
) -> dict[str, Any]:
    bind_llm(thread_id, llm_cfg)
    graph = get_triage_graph(db_factory=db_factory)
    config = {"configurable": {"thread_id": thread_id}}
    snap_db = db_factory()
    try:
        snapshot = step_snapshot(step, db=snap_db)
    finally:
        snap_db.close()
    init: TriageState = {
        "project_id": project_id,
        "run_id": run_id,
        "step_id": step.id,
        "case_id": step.case_id,
        "step_snapshot": snapshot,
        "plan": None,
        "user_decision": "pending",
        "apply_patch": False,
        "error": None,
        "thread_id": thread_id,
    }
    result = graph.invoke(init, config)
    return result if isinstance(result, dict) else {}


def resume_triage(
    *,
    db_factory,
    thread_id: str,
    action: str,
    apply_patch: bool,
) -> dict[str, Any]:
    graph = get_triage_graph(db_factory=db_factory)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        result = graph.invoke(Command(resume={"action": action, "apply_patch": apply_patch}), config)
        return result if isinstance(result, dict) else {}
    finally:
        unbind_llm(thread_id)
