"""Draft a strategy test plan from one requirement, then accept it into a plan."""

from __future__ import annotations

import base64
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.errors import AppError
from app.models.functional.requirement import Requirement
from app.models.functional.test_plan import TestPlan
from app.models.identity.user import User
from app.schemas.functional.test_plan import (
    LinkedTestPlanRef,
    PlanDraftChatMessage,
    TestPlanDraftAccept,
    TestPlanDraftChatIn,
    TestPlanDraftOut,
    TestPlanOut,
)
from app.services.ai.llm import chat_complete
from app.services.functional.requirement_content import (
    requirement_assets_for_ai,
    usable_requirement_text,
)
from app.user_messages import (
    REQUIREMENT_PLAN_DRAFT_CHAT_EMPTY,
    REQUIREMENT_PLAN_DRAFT_EMPTY,
    REQUIREMENT_PLAN_DRAFT_INVALID,
    REQUIREMENT_PLAN_DRAFT_MISSING,
    TEST_PLAN_DRAFT_PLAN_REQUIRED,
    TEST_PLAN_NOT_FOUND,
)

MAX_PLAN_CHARS = 12_000
MAX_CHAT_HISTORY = 20
_MISSING_ANALYSIS = "尚未完成需求分析"
_INITIAL_ASSISTANT = "已生成草稿，可直接说明修改意见。"
_HEADINGS = (
    "目标与范围、策略与优先级、环境与数据、人力与人天、"
    "进度与里程碑、进入标准 / 退出标准、风险与应对、交付物"
)

_SYSTEM = (
    "你是测试计划助手。根据需求整理一份策略与管理向的测试计划，用来指导后续测试深度和优先级。"
    "只写范围、策略、环境、人力与人天、进度、进出标准、风险、交付物。"
    "不要写用例步骤、预期结果、覆盖矩阵或用例提纲。"
    "人天不明时填「待确认」。"
    "只输出 Markdown，不要 JSON，不要用代码块包裹。"
    f"必须包含这些二级标题：{_HEADINGS}。"
    "策略、人天、里程碑、风险用 Markdown 表格。"
)

_CHAT_SYSTEM = (
    "你是测试计划助手。用户会给出当前测试计划草稿和修改意见。"
    "按意见修订整份草稿，保留策略与管理向内容，不要写用例步骤、预期结果或覆盖矩阵。"
    "人天不明时填「待确认」。"
    f"必须包含这些二级标题：{_HEADINGS}。"
    "策略、人天、里程碑、风险用 Markdown 表格。"
    '只输出 JSON 对象：{"reply":"一句简短中文说明本次改动","body_md":"完整 Markdown 正文"}。'
    "body_md 不要用代码块包裹，不要省略未改动的章节。"
)


def _analysis_payload(req: Requirement) -> dict | None:
    raw = req.analysis_json
    if not isinstance(raw, dict):
        return None
    features = raw.get("features") or []
    ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
    ui_summary = (ui.get("summary") or "").strip() if isinstance(ui, dict) else ""
    ui_pages = (ui.get("pages") or []) if isinstance(ui, dict) else []
    unclear = raw.get("unclear") or []
    if not (features or ui_summary or ui_pages or unclear):
        return None
    return {
        "features": features,
        "ui": {"summary": ui_summary, "pages": ui_pages},
        "unclear": unclear,
    }


def _user_text(title: str, text: str, analysis: dict | None) -> str:
    body = text.strip() or "（正文为空）"
    prompt = f"需求标题：{title}\n\n需求正文：\n{body}"
    if analysis is not None:
        dumped = json.dumps(analysis, ensure_ascii=False)
        prompt += (
            "\n\n需求分析（据此定范围、优先级和风险）：\n"
            f"{dumped}"
        )
    else:
        prompt += "\n\n当前没有需求分析。请在「风险与应对」中说明尚未完成需求分析。"
    return prompt


def _user_content(title: str, text: str, analysis: dict | None, images: list[tuple[str, bytes]]) -> str | list[dict]:
    prompt = _user_text(title, text, analysis)
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


def _messages(title: str, text: str, analysis: dict | None, images: list[tuple[str, bytes]]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_content(title, text, analysis, images)},
    ]


def _strip_fence(raw: str) -> str:
    text = (raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_markdown(raw: str) -> str:
    text = _strip_fence(raw)
    if not text:
        raise ValueError("plan markdown is empty")
    return text


def _ensure_missing_analysis_risk(body_md: str, has_analysis: bool) -> str:
    if has_analysis or _MISSING_ANALYSIS in body_md:
        return body_md
    extra = (
        "\n\n## 风险与应对\n\n"
        "| 风险 | 影响 | 应对 |\n"
        "|------|------|------|\n"
        f"| {_MISSING_ANALYSIS} | 范围、优先级和风险依据不足 | 建议先完成需求分析，再修订本计划 |\n"
    )
    return body_md.rstrip() + extra


def _description_from_markdown(body_md: str) -> str | None:
    paragraph = ""
    for line in body_md.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            if paragraph:
                break
            continue
        if text.startswith("|") or set(text) <= set("-|: "):
            if paragraph:
                break
            continue
        if text.startswith(("- ", "* ")):
            text = text[2:].strip()
        paragraph = f"{paragraph} {text}".strip() if paragraph else text
        if len(paragraph) > 240:
            break
    return _first_sentence(paragraph)


def _first_sentence(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    cut = len(raw)
    for index, char in enumerate(raw):
        if char in "。！？\n":
            cut = index
            break
    chunk = raw[:cut].strip()
    if not chunk:
        return None
    if len(chunk) > 200:
        chunk = chunk[:200].rstrip()
    return chunk


def _plan_name(given: str | None, title: str) -> str:
    name = (given or "").strip() or f"测试计划：{title}"
    return name[:512]


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


def _stored_messages(req: Requirement) -> list[PlanDraftChatMessage]:
    return [PlanDraftChatMessage(**m) for m in _normalize_messages(req.plan_draft_messages_json)]


def _draft_out(db: Session, req: Requirement) -> TestPlanDraftOut:
    return TestPlanDraftOut(
        requirement_id=req.id,
        body_md=req.plan_draft_md or "",
        drafted_at=req.plan_draft_at or now_beijing(),
        linked_plans=linked_plan_refs(db, req.project_id, req.id),
        messages=_stored_messages(req),
    )


def _parse_chat_payload(raw: str) -> tuple[str, str]:
    text = _strip_fence(raw)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("chat payload is not an object")
    reply = str(data.get("reply") or "").strip() or "已按你的意见更新草稿。"
    body_md = _parse_markdown(str(data.get("body_md") or ""))
    return reply, body_md


def linked_plan_refs(db: Session, project_id: int, requirement_id: int) -> list[LinkedTestPlanRef]:
    rows = (
        db.query(TestPlan)
        .filter(TestPlan.project_id == project_id, TestPlan.requirement_id == requirement_id)
        .order_by(TestPlan.id.desc())
        .all()
    )
    return [LinkedTestPlanRef(id=row.id, name=row.name) for row in rows]


def requirement_titles(db: Session, rows: list[TestPlan]) -> dict[int, str]:
    ids = {row.requirement_id for row in rows if row.requirement_id is not None}
    if not ids:
        return {}
    found = db.query(Requirement).filter(Requirement.id.in_(ids)).all()
    return {row.id: row.title for row in found}


def plan_to_out(db: Session, row: TestPlan, titles: dict[int, str] | None = None) -> TestPlanOut:
    title = None
    if row.requirement_id is not None:
        if titles is not None:
            title = titles.get(row.requirement_id)
        else:
            req = db.get(Requirement, row.requirement_id)
            title = req.title if req is not None else None
    content = row.body_md or None
    return TestPlanOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        version=row.version,
        iteration=row.iteration,
        description=row.description,
        status=row.status,
        requirement_id=row.requirement_id,
        requirement_title=title,
        body_md=content,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def generate_test_plan_draft(db: Session, req: Requirement) -> TestPlanDraftOut:
    analysis = _analysis_payload(req)
    raw_text, loaded_images = requirement_assets_for_ai(req)
    text = usable_requirement_text(raw_text, max_chars=MAX_PLAN_CHARS)
    images = [] if analysis is not None else loaded_images
    if not text and not images and analysis is None:
        raise AppError("bad_request", REQUIREMENT_PLAN_DRAFT_EMPTY, status_code=400)
    messages = _messages(req.title, text, analysis, images)
    raw = chat_complete(messages, db=db, temperature=0.2, json_mode=False, scene="测试计划草稿")
    try:
        parsed = _parse_markdown(raw)
    except Exception:
        retry = list(messages)
        retry.append({"role": "user", "content": "只输出 Markdown 正文，不要用代码块包裹。"})
        raw = chat_complete(retry, db=db, temperature=0, json_mode=False, scene="测试计划草稿")
        try:
            parsed = _parse_markdown(raw)
        except Exception as exc:
            raise AppError("bad_request", REQUIREMENT_PLAN_DRAFT_INVALID, status_code=400) from exc
    body_md = _ensure_missing_analysis_risk(parsed, analysis is not None)
    drafted_at = now_beijing()
    req.plan_draft_md = body_md
    req.plan_draft_at = drafted_at
    req.plan_draft_messages_json = [
        {"role": "assistant", "content": _INITIAL_ASSISTANT},
    ]
    db.commit()
    db.refresh(req)
    return _draft_out(db, req)


def chat_test_plan_draft(
    db: Session,
    req: Requirement,
    body: TestPlanDraftChatIn,
) -> TestPlanDraftOut:
    message = (body.message or "").strip()
    if not message:
        raise AppError("bad_request", REQUIREMENT_PLAN_DRAFT_CHAT_EMPTY, status_code=400)
    current = (body.body_md if body.body_md is not None else req.plan_draft_md) or ""
    current = current.strip()
    if not current:
        raise AppError("bad_request", REQUIREMENT_PLAN_DRAFT_MISSING, status_code=400)

    history = _normalize_messages(req.plan_draft_messages_json)[-MAX_CHAT_HISTORY:]
    llm_messages: list[dict] = [{"role": "system", "content": _CHAT_SYSTEM}]
    for item in history:
        llm_messages.append({"role": item["role"], "content": item["content"]})
    draft_for_prompt = current if len(current) <= MAX_PLAN_CHARS else current[:MAX_PLAN_CHARS]
    llm_messages.append(
        {
            "role": "user",
            "content": (
                f"当前测试计划草稿：\n{draft_for_prompt}\n\n"
                f"修改意见：\n{message}\n\n"
                "请输出 JSON：reply 与完整 body_md。"
            ),
        }
    )
    raw = chat_complete(
        llm_messages,
        db=db,
        temperature=0.2,
        json_mode=True,
        scene="测试计划草稿改稿",
    )
    try:
        reply, body_md = _parse_chat_payload(raw)
    except Exception:
        retry = list(llm_messages)
        retry.append(
            {
                "role": "user",
                "content": '只输出 JSON：{"reply":"...","body_md":"..."}，body_md 为完整 Markdown。',
            }
        )
        raw = chat_complete(
            retry,
            db=db,
            temperature=0,
            json_mode=True,
            scene="测试计划草稿改稿",
        )
        try:
            reply, body_md = _parse_chat_payload(raw)
        except Exception as exc:
            raise AppError("bad_request", REQUIREMENT_PLAN_DRAFT_INVALID, status_code=400) from exc

    analysis = _analysis_payload(req)
    body_md = _ensure_missing_analysis_risk(body_md, analysis is not None)
    drafted_at = now_beijing()
    next_messages = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]
    req.plan_draft_md = body_md
    req.plan_draft_at = drafted_at
    req.plan_draft_messages_json = next_messages
    db.commit()
    db.refresh(req)
    return _draft_out(db, req)


def accept_test_plan_draft(
    db: Session,
    req: Requirement,
    body: TestPlanDraftAccept,
    user: User,
) -> TestPlan:
    body_md = (body.body_md or "").strip()
    if not body_md:
        raise AppError("bad_request", REQUIREMENT_PLAN_DRAFT_INVALID, status_code=400)
    name = _plan_name(body.name, req.title)
    version = body.version.strip() if body.version else None
    iteration = body.iteration.strip() if body.iteration else None
    description = _description_from_markdown(body_md)
    existing: TestPlan | None = None
    if body.mode == "update":
        if body.plan_id is None:
            raise AppError("bad_request", TEST_PLAN_DRAFT_PLAN_REQUIRED, status_code=400)
        existing = (
            db.query(TestPlan)
            .filter(TestPlan.id == body.plan_id, TestPlan.project_id == req.project_id)
            .first()
        )
        if existing is None:
            raise AppError("not_found", TEST_PLAN_NOT_FOUND, status_code=404)

    now = now_beijing()
    req.plan_draft_md = body_md
    if req.plan_draft_at is None:
        req.plan_draft_at = now

    if existing is None:
        row = TestPlan(
            project_id=req.project_id,
            name=name,
            version=version,
            iteration=iteration,
            description=description,
            status="draft",
            requirement_id=req.id,
            body_md=body_md,
            creator_id=user.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    row = existing
    row.name = name
    row.version = version
    row.iteration = iteration
    row.description = description
    row.body_md = body_md
    db.commit()
    db.refresh(row)
    return row
