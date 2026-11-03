"""Analyze one requirement with text plus embedded document images."""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.errors import AppError
from app.models.functional.requirement import Requirement
from app.schemas.functional.requirement import (
    AnalysisChatIn,
    AnalysisChatMessage,
    AnalysisChatOut,
    AnalysisSaveIn,
    RequirementAnalysis,
)
from app.services.ai.llm import chat_complete
from app.services.functional.requirement_content import (
    requirement_assets_for_ai,
    usable_requirement_text,
)
from app.user_messages import (
    REQUIREMENT_ANALYSIS_CHAT_EMPTY,
    REQUIREMENT_ANALYSIS_EMPTY,
    REQUIREMENT_ANALYSIS_INVALID,
    REQUIREMENT_ANALYSIS_MISSING,
    REQUIREMENT_ANALYSIS_PENDING_MISSING,
)

log = logging.getLogger(__name__)

MAX_ANALYSIS_CHARS = 12_000
MAX_CHAT_HISTORY = 20
_INITIAL_ASSISTANT = "已生成分析，可直接说明修改意见。"

_ROOT_LIST_KEYS = ("features", "unclear")

_ANALYSIS_SCHEMA_HINT = (
    '{"features":[{"module":"模块名","items":[{"name":"功能名","detail":"说明"}]}],'
    '"ui":{"summary":"整体交互概括",'
    '"pages":[{"name":"页面或弹窗","description":"用途与布局","areas":["区域或控件"]}]},'
    '"unclear":[{"item":"待澄清问题","reason":"原因"}]}'
)

_SYSTEM = (
    "你是需求分析助手。根据需求标题、正文和附图（界面截图、流程图、表格、标注），"
    "整理三块内容，不要复述原文。"
    "features 是按模块划分的功能清单："
    "每项含 module（模块名）与 items（该模块下的功能）；"
    "items 每项含 name（功能名）与 detail（简要说明）。"
    "ui 是界面描述，且只能包含 summary 与 pages 两个键："
    "summary 用一两段话概括整体交互；"
    "pages 按页面或弹窗分条，每项含 name、description（页面用途与布局概述）、"
    "areas（主要区域或控件的短句数组）；"
    "无附图且正文无界面信息时 pages 可为空数组、summary 可为空字符串。"
    "写完 ui.pages 后必须立刻用 } 关闭 ui 对象，再写同级的 unclear。"
    "unclear 必须与 features、ui 同级，禁止放进 ui 里。"
    "unclear 是待澄清：缺失、看不清、缺标注，或附图与正文矛盾的问题。"
    "只输出 JSON 对象，不要 Markdown。"
    f"格式：{_ANALYSIS_SCHEMA_HINT}。"
    "数组都可以为空，ui.summary 可为空字符串，但不要省略字段。"
)

_CHAT_SYSTEM = (
    "你是需求分析助手。用户会给出当前需求分析 JSON 和修改意见。"
    "按意见修订完整分析结果，保留 features / ui / unclear 三块结构。"
    "features 按 module+items 分组；ui 只含 summary 与 pages（pages 项含 name、description、areas）；"
    "unclear 与 features、ui 同级。"
    "不要复述原文，不要输出 Markdown。"
    '只输出 JSON 对象：{"reply":"一句简短中文说明本次改动","analysis":'
    f"{_ANALYSIS_SCHEMA_HINT}"
    "}。"
    "analysis 必须是完整结果，不要省略未改动的章节；不要包含 image_count。"
)


def _user_content(title: str, text: str, images: list[tuple[str, bytes]]) -> str | list[dict]:
    body = text.strip() or "（正文为空，请只根据附图分析）"
    prompt = f"需求标题：{title}\n\n需求正文：\n{body}"
    if not images:
        return prompt
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for mime, blob in images:
        encoded = base64.standard_b64encode(blob).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }
        )
    return parts


def _messages(title: str, text: str, images: list[tuple[str, bytes]]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_content(title, text, images)},
    ]


def _strip_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _close_unbalanced(text: str) -> str:
    """Append missing ] / } so truncated or half-closed JSON can load."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
    if in_str:
        text += '"'
    suffix = "".join("}" if op == "{" else "]" for op in reversed(stack))
    return text + suffix if suffix else text


def _loads_json_object(raw: str) -> Any:
    text = _strip_fence(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _close_unbalanced(text)
        if repaired == text:
            raise
        return json.loads(repaired)


def _normalize_pages(pages: Any) -> list[dict[str, Any]]:
    if not isinstance(pages, list):
        return []
    out: list[dict[str, Any]] = []
    for pg in pages:
        if not isinstance(pg, dict):
            continue
        areas = pg.get("areas")
        if not isinstance(areas, list):
            areas = []
        out.append(
            {
                "name": str(pg.get("name") or "未命名"),
                "description": str(pg.get("description") or ""),
                "areas": [str(a).strip() for a in areas if str(a).strip()],
            }
        )
    return out


def _normalize_analysis_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Hoist root fields wrongly nested under ui (common LLM mistake)."""
    # Drop legacy analysis sections so old blobs / LLM leftovers do not confuse callers.
    for legacy in ("logic_issues", "points", "boundaries"):
        data.pop(legacy, None)
    ui = data.get("ui")
    if not isinstance(ui, dict):
        return data
    moved = False
    for key in _ROOT_LIST_KEYS:
        if key in ui and key not in data:
            data[key] = ui.pop(key)
            moved = True
        elif key in ui:
            ui.pop(key)
            moved = True
    # Also strip legacy keys nested under ui
    for legacy in ("logic_issues", "points", "boundaries"):
        if legacy in ui:
            ui.pop(legacy)
            moved = True
    if moved or any(k not in ("summary", "pages") for k in ui):
        data["ui"] = {
            "summary": str(ui.get("summary") or ""),
            "pages": _normalize_pages(ui.get("pages")),
        }
        if moved:
            log.info("需求分析 JSON 已纠正：将误放入 ui 的字段提升到根级")
    elif isinstance(ui.get("pages"), list):
        data["ui"] = {
            "summary": str(ui.get("summary") or ""),
            "pages": _normalize_pages(ui.get("pages")),
        }
    return data


def _parse_analysis(raw: str) -> RequirementAnalysis:
    data = _loads_json_object(raw)
    if not isinstance(data, dict):
        raise ValueError("analysis is not an object")
    data = _normalize_analysis_dict(data)
    return RequirementAnalysis.model_validate(data)


def _normalize_messages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant"):
            continue
        text = str(content or "").strip()
        if not text:
            continue
        out.append({"role": role, "content": text})
    return out


def _stored_messages(req: Requirement) -> list[AnalysisChatMessage]:
    return [AnalysisChatMessage(**m) for m in _normalize_messages(req.analysis_messages_json)]


def _analysis_from_stored(raw: Any) -> RequirementAnalysis | None:
    if not isinstance(raw, dict):
        return None
    try:
        data = _normalize_analysis_dict(json.loads(json.dumps(raw)))
        return RequirementAnalysis.model_validate(data)
    except Exception:
        return None


def _dump_analysis(parsed: RequirementAnalysis, *, image_count: int | None = None) -> dict[str, Any]:
    saved = parsed.model_dump()
    if image_count is not None:
        saved["image_count"] = image_count
    elif "image_count" not in saved:
        saved["image_count"] = 0
    return saved


def _chat_out(
    req: Requirement,
    *,
    reply: str = "",
    pending: RequirementAnalysis | None = None,
) -> AnalysisChatOut:
    analysis = _analysis_from_stored(req.analysis_json)
    if analysis is None:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_MISSING, status_code=400)
    pending_out = pending
    if pending_out is None:
        pending_out = _analysis_from_stored(req.analysis_pending_json)
    return AnalysisChatOut(
        requirement_id=req.id,
        analysis=analysis,
        pending_analysis=pending_out,
        analyzed_at=req.analyzed_at,
        messages=_stored_messages(req),
        reply=reply,
    )


def _parse_chat_payload(raw: str) -> tuple[str, RequirementAnalysis]:
    data = _loads_json_object(raw)
    if not isinstance(data, dict):
        raise ValueError("chat payload is not an object")
    reply = str(data.get("reply") or "").strip() or "已按你的意见更新分析。"
    analysis_raw = data.get("analysis")
    if not isinstance(analysis_raw, dict):
        raise ValueError("analysis is missing")
    analysis_raw = _normalize_analysis_dict(analysis_raw)
    analysis_raw.pop("image_count", None)
    return reply, RequirementAnalysis.model_validate(analysis_raw)


def analyze_requirement(db: Session, req: Requirement) -> Requirement:
    raw_text, images = requirement_assets_for_ai(req)
    text = usable_requirement_text(raw_text, max_chars=MAX_ANALYSIS_CHARS)
    if not text and not images:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_EMPTY, status_code=400)
    messages = _messages(req.title, text, images)
    raw = chat_complete(messages, db=db, temperature=0.2, scene="需求分析")
    try:
        parsed = _parse_analysis(raw)
    except Exception as first_err:
        log.warning("需求分析首次解析失败，正在重试: %s", first_err)
        retry = list(messages)
        retry.append({"role": "assistant", "content": raw})
        retry.append(
            {
                "role": "user",
                "content": (
                    "上一段输出不是合法且符合 schema 的 JSON。"
                    "请只输出一个 JSON 对象：features、ui、unclear 必须同级；"
                    "features 按 module+items 分组；"
                    "ui 只能含 summary 与 pages（pages 项含 name、description、areas），"
                    "写完 pages 后立刻用 } 关闭 ui；不要 Markdown。"
                    f"（解析错误: {first_err}）"
                ),
            }
        )
        raw = chat_complete(retry, db=db, temperature=0, scene="需求分析")
        try:
            parsed = _parse_analysis(raw)
        except Exception as e:
            raise AppError("bad_request", REQUIREMENT_ANALYSIS_INVALID, status_code=400) from e
    req.analysis_json = _dump_analysis(parsed, image_count=len(images))
    req.analyzed_at = now_beijing()
    req.analysis_pending_json = None
    req.analysis_messages_json = [{"role": "assistant", "content": _INITIAL_ASSISTANT}]
    db.commit()
    db.refresh(req)
    return req


def chat_requirement_analysis(
    db: Session,
    req: Requirement,
    body: AnalysisChatIn,
) -> AnalysisChatOut:
    message = (body.message or "").strip()
    if not message:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_CHAT_EMPTY, status_code=400)

    current: RequirementAnalysis | None = None
    if body.analysis is not None:
        current = body.analysis
    elif req.analysis_pending_json is not None:
        current = _analysis_from_stored(req.analysis_pending_json)
    else:
        current = _analysis_from_stored(req.analysis_json)
    if current is None:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_MISSING, status_code=400)

    baseline = _analysis_from_stored(req.analysis_json)
    image_count = int((baseline.image_count if baseline else 0) or current.image_count or 0)

    history = _normalize_messages(req.analysis_messages_json)[-MAX_CHAT_HISTORY:]
    current_payload = current.model_dump()
    current_payload.pop("image_count", None)
    draft_json = json.dumps(current_payload, ensure_ascii=False)
    if len(draft_json) > MAX_ANALYSIS_CHARS:
        draft_json = draft_json[:MAX_ANALYSIS_CHARS]

    llm_messages: list[dict] = [{"role": "system", "content": _CHAT_SYSTEM}]
    for item in history:
        llm_messages.append({"role": item["role"], "content": item["content"]})
    llm_messages.append(
        {
            "role": "user",
            "content": (
                f"当前需求分析 JSON：\n{draft_json}\n\n"
                f"修改意见：\n{message}\n\n"
                "请输出 JSON：reply 与完整 analysis。"
            ),
        }
    )
    raw = chat_complete(
        llm_messages,
        db=db,
        temperature=0.2,
        json_mode=True,
        scene="需求分析改稿",
    )
    try:
        reply, pending = _parse_chat_payload(raw)
    except Exception:
        retry = list(llm_messages)
        retry.append(
            {
                "role": "user",
                "content": (
                    '只输出 JSON：{"reply":"...","analysis":{...}}，'
                    "analysis 含完整 features、ui、unclear。"
                ),
            }
        )
        raw = chat_complete(
            retry,
            db=db,
            temperature=0,
            json_mode=True,
            scene="需求分析改稿",
        )
        try:
            reply, pending = _parse_chat_payload(raw)
        except Exception as exc:
            raise AppError("bad_request", REQUIREMENT_ANALYSIS_INVALID, status_code=400) from exc

    pending_dump = _dump_analysis(pending, image_count=image_count)
    next_messages = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]
    req.analysis_pending_json = pending_dump
    req.analysis_messages_json = next_messages
    db.commit()
    db.refresh(req)
    return _chat_out(
        req,
        reply=reply,
        pending=RequirementAnalysis.model_validate(pending_dump),
    )


def accept_requirement_analysis(db: Session, req: Requirement) -> AnalysisChatOut:
    pending = _analysis_from_stored(req.analysis_pending_json)
    if pending is None:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_PENDING_MISSING, status_code=400)
    baseline = _analysis_from_stored(req.analysis_json)
    image_count = int(
        (pending.image_count or 0)
        or (baseline.image_count if baseline else 0)
        or 0
    )
    req.analysis_json = _dump_analysis(pending, image_count=image_count)
    req.analyzed_at = now_beijing()
    req.analysis_pending_json = None
    db.commit()
    db.refresh(req)
    return _chat_out(req, reply="已应用修改。")


def discard_requirement_analysis(db: Session, req: Requirement) -> AnalysisChatOut:
    if req.analysis_pending_json is None:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_PENDING_MISSING, status_code=400)
    req.analysis_pending_json = None
    db.commit()
    db.refresh(req)
    return _chat_out(req, reply="已放弃修改。")


def save_requirement_analysis(
    db: Session,
    req: Requirement,
    body: AnalysisSaveIn,
) -> AnalysisChatOut:
    if _analysis_from_stored(req.analysis_json) is None:
        raise AppError("bad_request", REQUIREMENT_ANALYSIS_MISSING, status_code=400)

    baseline = _analysis_from_stored(req.analysis_json)
    image_count = int(
        (body.analysis.image_count or 0)
        or (baseline.image_count if baseline else 0)
        or 0
    )
    dumped = _dump_analysis(body.analysis, image_count=image_count)

    if req.analysis_pending_json is not None:
        req.analysis_pending_json = dumped
        db.commit()
        db.refresh(req)
        return _chat_out(
            req,
            reply="已保存到待确认稿。",
            pending=RequirementAnalysis.model_validate(dumped),
        )

    req.analysis_json = dumped
    req.analyzed_at = now_beijing()
    db.commit()
    db.refresh(req)
    return _chat_out(req, reply="已保存分析。")
