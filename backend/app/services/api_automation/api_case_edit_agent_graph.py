"""LangGraph: batch revise existing ApiCase rows (multi-turn + confirm apply)."""

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
    ApiCaseEditAgentSession,
    ApiDefinition,
)
from app.services.ai.effective_ai_settings import LlmRuntime
from app.services.api_automation.api_scenario_events import emit_log, get_event_bus
from app.services.api_automation.api_scenario_llm import llm_json

log = logging.getLogger(__name__)

_checkpointer = MemorySaver()
_compiled = None
_llm_by_thread: dict[str, LlmRuntime] = {}

MAX_CASES = 20
_MAX_CASES = MAX_CASES
_DRAFT_KEYS = (
    "name",
    "method",
    "path",
    "query",
    "headers",
    "body_type",
    "body",
    "assertions",
    "extractors",
    "timeout_ms",
    "description",
    "tags",
    "status",
)
_ENDPOINT_CHANGE_RE = re.compile(
    r"(改\s*路径|修改\s*路径|改\s*path|修改\s*path|改\s*方法|修改\s*方法|"
    r"改\s*method|修改\s*method|更换\s*路径|更换\s*方法|改\s*endpoint)",
    re.I,
)


class EditAgentState(TypedDict, total=False):
    project_id: int
    user_id: int
    prompt: str
    case_ids: list[int]
    base_snapshots: list[dict[str, Any]]
    definition_summaries: list[dict[str, Any]]
    messages: list[dict[str, str]]
    plan: dict[str, Any] | None
    user_decision: str | None
    revise_message: str | None
    apply_case_ids: list[int] | None
    allow_endpoint_change: bool
    applied_case_ids: list[int] | None
    error: str | None
    thread_id: str


def bind_llm(thread_id: str, cfg: LlmRuntime) -> None:
    _llm_by_thread[thread_id] = cfg


def unbind_llm(thread_id: str) -> None:
    _llm_by_thread.pop(thread_id, None)


def _system_prompt(*, allow_endpoint_change: bool) -> str:
    endpoint_rule = (
        "允许按用户要求修改 method/path。"
        if allow_endpoint_change
        else "禁止修改 method/path（即使草稿里写了也无效，服务端会强制保留原值）。"
    )
    return (
        "你是接口「单接口用例批量修订」助手。用户选中了多条已有用例，"
        "请按同一类修改意图对范围内每条用例给出修订草稿。"
        "只输出一个 JSON 对象，不要 Markdown。格式："
        '{"title":"...","summary":"...","revisions":['
        '{"case_id":12,"name":"...","summary":"一句话改动",'
        '"changed_fields":["headers","assertions"],"skipped":false,"skip_reason":null,'
        '"draft":{"name":"...","method":"POST","path":"/x","query":{},"headers":{},'
        '"body_type":"json","body":"...","assertions":[{"type":"status","expected":200}],'
        '"extractors":[],"timeout_ms":15000,"description":"","tags":[],"status":"draft"}'
        '}],"warnings":[]}'
        "设计规则："
        "1) 必须覆盖范围内每一条 case_id；无法改的设 skipped=true 并写 skip_reason；"
        "2) draft 尽量返回完整字段；未改字段可省略，服务端会用快照合并；"
        "3) assertions 优先 status + jsonpath(eq/exists/contains/regex)；禁止编造随机 expected；"
        "4) 跨用例依赖用 {{var}} 占位，并写入 warnings；"
        f"5) {endpoint_rule}"
        "6) changed_fields 只列实际变更的字段名。"
    )


def _log(state: EditAgentState, node: str, message: str, level: str = "info", **data: Any) -> None:
    tid = str(state.get("thread_id") or "")
    if not tid:
        return
    emit_log(tid, node=node, message=message, level=level, data=data or None)


def _as_str_dict(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _detect_allow_endpoint_change(*texts: str) -> bool:
    for text in texts:
        if text and _ENDPOINT_CHANGE_RE.search(text):
            return True
    return False


def case_to_snapshot(row: ApiCase, *, definition: ApiDefinition | None = None) -> dict[str, Any]:
    return {
        "case_id": int(row.id),
        "api_id": int(row.api_id) if row.api_id is not None else None,
        "name": row.name or "",
        "method": (row.method or "GET").upper(),
        "path": row.path or "/",
        "query": _as_str_dict(row.query_json),
        "headers": _as_str_dict(row.headers_json),
        "body_type": row.body_type or "none",
        "body": row.body_text or "",
        "assertions": list(row.assertions_json) if isinstance(row.assertions_json, list) else [],
        "extractors": list(row.extractors_json) if isinstance(row.extractors_json, list) else [],
        "timeout_ms": int(row.timeout_ms or 15000),
        "description": row.description or "",
        "tags": list(row.tags_json) if isinstance(row.tags_json, list) else [],
        "status": row.status or "draft",
        "definition_name": (definition.name if definition else "") or "",
        "definition_method": (definition.method if definition else "") or "",
        "definition_path": (definition.path if definition else "") or "",
    }


def _normalize_assertion(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    t = str(raw.get("type") or "").strip()
    if not t:
        return None
    out: dict[str, Any] = {"type": t}
    if "op" in raw and raw.get("op") is not None:
        out["op"] = str(raw.get("op"))
    if "expr" in raw and raw.get("expr") is not None:
        out["expr"] = str(raw.get("expr"))
    if "expected" in raw:
        out["expected"] = raw.get("expected")
    return out


def _normalize_extractor(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    expr = str(raw.get("expr") or "").strip()
    if not name or not expr:
        return None
    out: dict[str, Any] = {
        "name": name[:128],
        "type": str(raw.get("type") or "jsonpath"),
        "expr": expr,
    }
    if raw.get("from"):
        out["from"] = str(raw.get("from"))
    return out


def _merge_draft(base: dict[str, Any], proposed: Any) -> dict[str, Any]:
    out = {
        "name": str(base.get("name") or "未命名")[:512],
        "method": str(base.get("method") or "GET").upper(),
        "path": str(base.get("path") or "/"),
        "query": _as_str_dict(base.get("query")),
        "headers": _as_str_dict(base.get("headers")),
        "body_type": str(base.get("body_type") or "none"),
        "body": str(base.get("body") or ""),
        "assertions": list(base.get("assertions") or []),
        "extractors": list(base.get("extractors") or []),
        "timeout_ms": int(base.get("timeout_ms") or 15000),
        "description": str(base.get("description") or ""),
        "tags": [str(t) for t in (base.get("tags") or []) if str(t or "").strip()],
        "status": str(base.get("status") or "draft"),
    }
    if not isinstance(proposed, dict):
        return out
    for key in _DRAFT_KEYS:
        if key not in proposed:
            continue
        value = proposed[key]
        if value is None and key not in ("description",):
            continue
        if key in ("query", "headers"):
            if isinstance(value, dict):
                out[key] = _as_str_dict(value)
            continue
        if key == "assertions":
            if isinstance(value, list):
                assertions = []
                for a in value:
                    na = _normalize_assertion(a)
                    if na:
                        assertions.append(na)
                if not any(a.get("type") == "status" for a in assertions):
                    assertions.insert(0, {"type": "status", "expected": 200})
                out[key] = assertions
            continue
        if key == "extractors":
            if isinstance(value, list):
                extractors = []
                for e in value:
                    ne = _normalize_extractor(e)
                    if ne:
                        extractors.append(ne)
                out[key] = extractors
            continue
        if key == "body":
            if isinstance(value, str):
                out["body"] = value
            else:
                try:
                    out["body"] = json.dumps(value, ensure_ascii=False)
                except (TypeError, ValueError):
                    out["body"] = str(value)
            continue
        if key == "timeout_ms":
            try:
                out["timeout_ms"] = max(1000, min(int(value or 15000), 120000))
            except (TypeError, ValueError):
                pass
            continue
        if key == "tags":
            if isinstance(value, list):
                out["tags"] = [str(t).strip() for t in value if str(t or "").strip()]
            continue
        if key in ("name", "method", "path", "body_type", "description", "status"):
            out[key] = str(value or "")[:2000] if key == "description" else str(value or "")
            if key == "method":
                out["method"] = out["method"].upper() or "GET"
            if key == "name":
                out["name"] = (out["name"] or "未命名")[:512]
            if key == "path":
                out["path"] = out["path"] or "/"
    return out


def _diff_fields(base: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key in _DRAFT_KEYS:
        if json.dumps(base.get(key), ensure_ascii=False, sort_keys=True) != json.dumps(
            draft.get(key), ensure_ascii=False, sort_keys=True
        ):
            changed.append(key)
    return changed


def normalize_edit_plan(
    plan: dict[str, Any] | None,
    *,
    snapshots_by_id: dict[int, dict[str, Any]],
    allow_endpoint_change: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    if plan is None:
        return {"title": "", "summary": "", "revisions": [], "warnings": []}, []
    if not isinstance(plan, dict):
        return None, ["计划不是对象"]
    raw_revs = plan.get("revisions")
    if raw_revs is None:
        raw_revs = []
    if not isinstance(raw_revs, list):
        return None, ["revisions 必须是数组"]

    warnings = [str(w) for w in (plan.get("warnings") or []) if str(w or "").strip()]
    # 空 revisions：等待用户首条修改说明，不伪造 skipped 行
    if len(raw_revs) == 0:
        return {
            "title": str(plan.get("title") or "")[:200],
            "summary": str(plan.get("summary") or "")[:2000],
            "revisions": [],
            "warnings": warnings,
        }, []

    by_case: dict[int, dict[str, Any]] = {}
    for raw in raw_revs:
        if not isinstance(raw, dict):
            continue
        try:
            cid = int(raw.get("case_id"))
        except (TypeError, ValueError):
            continue
        if cid not in snapshots_by_id:
            warnings.append(f"忽略范围外的 case_id={cid}")
            continue
        base = snapshots_by_id[cid]
        skipped = bool(raw.get("skipped"))
        skip_reason = str(raw.get("skip_reason") or "").strip() or None
        if skipped:
            by_case[cid] = {
                "case_id": cid,
                "name": str(raw.get("name") or base.get("name") or ""),
                "summary": str(raw.get("summary") or skip_reason or "已跳过"),
                "changed_fields": [],
                "skipped": True,
                "skip_reason": skip_reason or "模型标记跳过",
                "draft": _merge_draft(base, None),
            }
            continue
        draft = _merge_draft(base, raw.get("draft") if isinstance(raw.get("draft"), dict) else raw)
        if not allow_endpoint_change:
            draft["method"] = str(base.get("method") or "GET").upper()
            draft["path"] = str(base.get("path") or "/")
        changed = _diff_fields(
            {
                k: base.get(k)
                for k in _DRAFT_KEYS
            },
            draft,
        )
        # ensure method/path not listed when locked
        if not allow_endpoint_change:
            changed = [c for c in changed if c not in ("method", "path")]
        model_fields = [str(x) for x in (raw.get("changed_fields") or []) if str(x or "").strip()]
        changed_fields = model_fields or changed
        if not allow_endpoint_change:
            changed_fields = [c for c in changed_fields if c not in ("method", "path")]
        by_case[cid] = {
            "case_id": cid,
            "name": str(raw.get("name") or draft.get("name") or base.get("name") or ""),
            "summary": str(raw.get("summary") or "").strip() or ("无实质变更" if not changed else "已修订"),
            "changed_fields": changed_fields,
            "skipped": False,
            "skip_reason": None,
            "draft": draft,
        }

    revisions: list[dict[str, Any]] = []
    for cid, base in snapshots_by_id.items():
        if cid in by_case:
            revisions.append(by_case[cid])
        else:
            revisions.append(
                {
                    "case_id": cid,
                    "name": str(base.get("name") or ""),
                    "summary": "未返回修订，已跳过",
                    "changed_fields": [],
                    "skipped": True,
                    "skip_reason": "模型未返回该用例",
                    "draft": _merge_draft(base, None),
                }
            )

    return {
        "title": str(plan.get("title") or "批量修订")[:200],
        "summary": str(plan.get("summary") or "")[:2000],
        "revisions": revisions,
        "warnings": warnings,
    }, []


def load_case_snapshots(
    db: Session, project_id: int, case_ids: list[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = (
        db.query(ApiCase)
        .filter(ApiCase.project_id == project_id, ApiCase.id.in_(case_ids))
        .all()
    )
    by_id = {int(r.id): r for r in rows}
    api_ids = [int(r.api_id) for r in rows if r.api_id is not None]
    defs: dict[int, ApiDefinition] = {}
    if api_ids:
        for d in (
            db.query(ApiDefinition)
            .filter(ApiDefinition.project_id == project_id, ApiDefinition.id.in_(api_ids))
            .all()
        ):
            defs[int(d.id)] = d
    snapshots: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for cid in case_ids:
        row = by_id.get(cid)
        if row is None:
            continue
        definition = defs.get(int(row.api_id)) if row.api_id is not None else None
        snap = case_to_snapshot(row, definition=definition)
        snapshots.append(snap)
        summaries.append(
            {
                "case_id": snap["case_id"],
                "name": snap["name"],
                "method": snap["method"],
                "path": snap["path"],
                "api_id": snap["api_id"],
                "definition_name": snap.get("definition_name") or "",
            }
        )
    return snapshots, summaries


def load_cases(state: EditAgentState, *, db: Session) -> dict[str, Any]:
    _log(state, "load_cases", "加载选中用例快照")
    case_ids = [int(x) for x in (state.get("case_ids") or [])]
    snapshots, summaries = load_case_snapshots(db, int(state["project_id"]), case_ids)
    if len(snapshots) != len(case_ids):
        found = {int(s["case_id"]) for s in snapshots}
        missing = [c for c in case_ids if c not in found]
        msg = f"部分用例不存在或不属于当前项目: {missing}"
        _log(state, "load_cases", msg, level="error")
        return {"error": msg, "base_snapshots": snapshots, "definition_summaries": summaries}
    _log(state, "load_cases", f"已加载 {len(snapshots)} 条用例快照")
    return {
        "base_snapshots": snapshots,
        "definition_summaries": summaries,
        "error": None,
        "allow_endpoint_change": _detect_allow_endpoint_change(state.get("prompt") or ""),
    }


def _compact_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in snapshots:
        out.append({k: s.get(k) for k in ("case_id", "name", *_DRAFT_KEYS, "api_id", "definition_name")})
    return out


def revise_batch(state: EditAgentState) -> dict[str, Any]:
    if state.get("error") and not state.get("base_snapshots"):
        return {}
    msg = (state.get("revise_message") or state.get("prompt") or "").strip()
    if not msg:
        empty = {"title": "", "summary": "等待修改说明", "revisions": [], "warnings": []}
        _log(state, "revise_batch", "无修改说明，进入空计划等待用户输入")
        return {
            "plan": empty,
            "revise_message": None,
            "user_decision": "pending",
            "error": None,
        }

    allow = bool(state.get("allow_endpoint_change")) or _detect_allow_endpoint_change(msg)
    cfg = _llm_by_thread.get(str(state.get("thread_id") or ""))
    if cfg is None:
        return {"error": "LLM 未配置"}

    chat = list(state.get("messages") or [])
    chat.append({"role": "user", "content": msg})
    current = state.get("plan") or {}
    snapshots = state.get("base_snapshots") or []
    messages = [
        {"role": "system", "content": _system_prompt(allow_endpoint_change=allow)},
        {
            "role": "user",
            "content": (
                f"用例快照 JSON：\n{json.dumps(_compact_snapshots(snapshots), ensure_ascii=False)}\n\n"
                f"当前修订计划 JSON：\n{json.dumps(current, ensure_ascii=False)}\n\n"
                f"用户修改要求：\n{msg}\n\n"
                "请输出修订后的完整 revisions JSON（不要只返回 diff）。"
            ),
        },
    ]
    _log(state, "revise_batch", f"调用 LLM 修订: {msg[:120]}")
    try:
        plan = llm_json(messages, cfg=cfg, scene="接口用例批量修订")
    except Exception as e:  # noqa: BLE001
        log.exception("case edit agent revise_batch failed")
        _log(state, "revise_batch", f"修改失败: {e}", level="error")
        return {"error": f"修改计划失败: {e}", "revise_message": None}

    title = str(plan.get("title") or "批量修订") if isinstance(plan, dict) else "批量修订"
    chat.append({"role": "assistant", "content": f"已按你的要求更新修订草稿：{title}"})
    _log(state, "revise_batch", "修订草稿已生成", title=title)
    return {
        "plan": plan if isinstance(plan, dict) else {},
        "messages": chat,
        "revise_message": None,
        "user_decision": "pending",
        "allow_endpoint_change": allow,
        "error": None,
    }


def validate_plan(state: EditAgentState) -> dict[str, Any]:
    if state.get("error") and not state.get("base_snapshots"):
        return {}
    _log(state, "validate_plan", "开始校验修订计划")
    snapshots = state.get("base_snapshots") or []
    by_id = {int(s["case_id"]): s for s in snapshots}
    plan, errs = normalize_edit_plan(
        state.get("plan"),
        snapshots_by_id=by_id,
        allow_endpoint_change=bool(state.get("allow_endpoint_change")),
    )
    if plan is None:
        msg = "; ".join(errs) if errs else "计划校验失败"
        _log(state, "validate_plan", f"校验失败: {msg}", level="error")
        return {"error": msg}
    active = sum(1 for r in plan["revisions"] if not r.get("skipped"))
    _log(state, "validate_plan", f"校验通过，可应用 {active}/{len(plan['revisions'])} 条")
    return {"plan": plan, "error": None}


def wait_user_confirm(state: EditAgentState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    tid = str(state.get("thread_id") or "")
    plan = state.get("plan")
    get_event_bus().append(tid, {"type": "plan", "plan": plan})
    get_event_bus().append(
        tid,
        {"type": "interrupted", "message": "可继续对话修改修订草稿，或勾选后确认写回用例"},
    )
    _log(state, "wait_user_confirm", "已暂停，等待用户确认或继续修改")
    decision = interrupt({"plan": plan, "message": "等待用户确认或继续修改"})
    if not isinstance(decision, dict):
        decision = {"action": "cancel"}
    action = str(decision.get("action") or "cancel")
    revise_message = str(decision.get("message") or "").strip() if action == "revise" else None
    apply_ids = None
    if action == "confirm":
        raw_ids = decision.get("case_ids")
        if isinstance(raw_ids, list):
            apply_ids = []
            for x in raw_ids:
                try:
                    apply_ids.append(int(x))
                except (TypeError, ValueError):
                    continue
    allow = bool(state.get("allow_endpoint_change"))
    if revise_message:
        allow = allow or _detect_allow_endpoint_change(revise_message)
    _log(state, "wait_user_confirm", f"用户决定: {action}")
    return {
        "user_decision": action,
        "revise_message": revise_message,
        "apply_case_ids": apply_ids,
        "allow_endpoint_change": allow,
        "error": None,
    }


def apply_revisions_from_plan(
    db: Session,
    project_id: int,
    plan: dict[str, Any] | None,
    case_ids: list[int] | None = None,
) -> list[int]:
    """Persist revision drafts from a saved plan onto ApiCase rows (DB-driven)."""
    revisions = plan.get("revisions") if isinstance(plan, dict) and isinstance(plan.get("revisions"), list) else []
    wanted_set = set(int(x) for x in case_ids) if isinstance(case_ids, list) and case_ids else None
    applied: list[int] = []
    for rev in revisions:
        if not isinstance(rev, dict) or rev.get("skipped"):
            continue
        try:
            cid = int(rev.get("case_id"))
        except (TypeError, ValueError):
            continue
        if wanted_set is not None and cid not in wanted_set:
            continue
        draft = rev.get("draft") if isinstance(rev.get("draft"), dict) else None
        if not draft:
            continue
        row = (
            db.query(ApiCase)
            .filter(ApiCase.id == cid, ApiCase.project_id == project_id)
            .first()
        )
        if row is None:
            continue
        row.name = str(draft.get("name") or row.name)[:512]
        row.method = str(draft.get("method") or row.method or "GET").upper()
        row.path = str(draft.get("path") or row.path or "/")
        row.query_json = _as_str_dict(draft.get("query"))
        row.headers_json = _as_str_dict(draft.get("headers"))
        row.body_type = str(draft.get("body_type") or row.body_type or "none")
        row.body_text = str(draft.get("body") if draft.get("body") is not None else (row.body_text or ""))
        row.assertions_json = list(draft.get("assertions") or [])
        row.extractors_json = list(draft.get("extractors") or [])
        try:
            row.timeout_ms = max(1000, min(int(draft.get("timeout_ms") or row.timeout_ms or 15000), 120000))
        except (TypeError, ValueError):
            pass
        if "description" in draft:
            row.description = str(draft.get("description") or "")
        if "tags" in draft and isinstance(draft.get("tags"), list):
            row.tags_json = [str(t) for t in draft["tags"] if str(t or "").strip()]
        if "status" in draft and draft.get("status"):
            row.status = str(draft.get("status"))
        db.add(row)
        applied.append(cid)
    db.commit()
    return applied


def apply_updates(state: EditAgentState, *, db: Session) -> dict[str, Any]:
    if state.get("error"):
        return {}
    if state.get("user_decision") != "confirm":
        _log(state, "apply_updates", "用户取消，跳过写回")
        return {"applied_case_ids": []}
    plan = state.get("plan") or {}
    revisions = plan.get("revisions") if isinstance(plan.get("revisions"), list) else []
    _log(state, "apply_updates", f"开始写回用例，候选 {len(revisions)}")
    applied = apply_revisions_from_plan(
        db,
        int(state["project_id"]),
        plan if isinstance(plan, dict) else None,
        state.get("apply_case_ids"),
    )
    _log(state, "apply_updates", f"已写回 {len(applied)} 条用例")
    return {"applied_case_ids": applied}


def finalize(state: EditAgentState) -> dict[str, Any]:
    tid = str(state.get("thread_id") or "")
    if state.get("error") and state.get("user_decision") not in ("confirm", "cancel", "revise"):
        get_event_bus().append(tid, {"type": "error", "message": state.get("error")})
        get_event_bus().append(
            tid,
            {"type": "done", "status": "error", "applied_case_ids": []},
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
            "applied_case_ids": state.get("applied_case_ids") or [],
        },
    )
    _log(state, "finalize", f"结束 status={status}")
    return {}


def _route_after_load(state: EditAgentState) -> Literal["revise_batch", "finalize"]:
    if state.get("error"):
        return "finalize"
    return "revise_batch"


def _route_after_validate(state: EditAgentState) -> Literal["wait_user_confirm", "finalize"]:
    if state.get("error"):
        return "finalize"
    return "wait_user_confirm"


def _route_after_confirm(
    state: EditAgentState,
) -> Literal["apply_updates", "revise_batch", "finalize"]:
    decision = state.get("user_decision")
    if decision == "confirm":
        return "apply_updates"
    if decision == "revise":
        return "revise_batch"
    return "finalize"


def build_graph(*, db_factory):
    def _load(state: EditAgentState) -> dict[str, Any]:
        db = db_factory()
        try:
            return load_cases(state, db=db)
        finally:
            db.close()

    def _apply(state: EditAgentState) -> dict[str, Any]:
        db = db_factory()
        try:
            return apply_updates(state, db=db)
        finally:
            db.close()

    g = StateGraph(EditAgentState)
    g.add_node("load_cases", _load)
    g.add_node("revise_batch", revise_batch)
    g.add_node("validate_plan", validate_plan)
    g.add_node("wait_user_confirm", wait_user_confirm)
    g.add_node("apply_updates", _apply)
    g.add_node("finalize", finalize)

    g.add_edge(START, "load_cases")
    g.add_conditional_edges("load_cases", _route_after_load)
    g.add_edge("revise_batch", "validate_plan")
    g.add_conditional_edges("validate_plan", _route_after_validate)
    g.add_conditional_edges("wait_user_confirm", _route_after_confirm)
    g.add_edge("apply_updates", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=_checkpointer)


def get_compiled_graph(*, db_factory):
    global _compiled
    if _compiled is None:
        _compiled = build_graph(db_factory=db_factory)
    return _compiled


def reset_compiled_graph() -> None:
    """Drop compiled graph and in-memory checkpoints (simulates process reload)."""
    global _compiled, _checkpointer
    _compiled = None
    _checkpointer = MemorySaver()


def new_thread_id() -> str:
    return uuid.uuid4().hex


def persist_session_events(db: Session, session: ApiCaseEditAgentSession) -> None:
    events = get_event_bus().snapshot(session.thread_id)
    session.events_json = events
    db.add(session)
    db.commit()


def sync_session_from_state(
    db: Session,
    session: ApiCaseEditAgentSession,
    *,
    plan: dict | None,
    applied_case_ids: list[int] | None,
    status: str,
    error: str | None = None,
    messages: list[dict[str, str]] | None = None,
    base_snapshots: list[dict[str, Any]] | None = None,
) -> None:
    session.plan_json = plan
    session.applied_case_ids_json = applied_case_ids
    session.status = status
    if messages is not None:
        session.messages_json = messages
    if base_snapshots is not None:
        session.base_snapshots_json = base_snapshots
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
    case_ids: list[int],
    llm_cfg: LlmRuntime,
) -> dict[str, Any]:
    bind_llm(thread_id, llm_cfg)
    graph = get_compiled_graph(db_factory=db_factory)
    config = {"configurable": {"thread_id": thread_id}}
    init: EditAgentState = {
        "project_id": project_id,
        "user_id": user_id,
        "prompt": prompt or "",
        "case_ids": case_ids,
        "base_snapshots": [],
        "definition_summaries": [],
        "messages": [],
        "plan": None,
        "user_decision": "pending",
        "revise_message": None,
        "apply_case_ids": None,
        "allow_endpoint_change": False,
        "applied_case_ids": None,
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
    case_ids: list[int] | None = None,
    unbind: bool = True,
) -> dict[str, Any]:
    graph = get_compiled_graph(db_factory=db_factory)
    config = {"configurable": {"thread_id": thread_id}}
    payload: dict[str, Any] = {"action": action}
    if message is not None:
        payload["message"] = message
    if case_ids is not None:
        payload["case_ids"] = case_ids
    try:
        result = graph.invoke(Command(resume=payload), config)
        return result if isinstance(result, dict) else {}
    finally:
        if unbind and action in ("confirm", "cancel"):
            unbind_llm(thread_id)
