import base64
import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.functional.testcases import _case_to_out
from app.deps import SessionLocal, get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.functional.requirement import Requirement
from app.models.functional.testcase import TestCase, TestCaseStep
from app.models.identity.user import User
from app.schemas.ai.ai_gen import (
    AiConfirmRequest,
    AiGenerateRequest,
    AiGenerateResponse,
    ChatMessage,
    PromptMessage,
    GeneratedCase,
    LlmOptionsOut,
)
from app.schemas.functional.testcase import TestCaseOut, clean_case_type, clean_precondition
from app.services.ai import llm
from app.services.ai.fast_case_skill import FastCaseSkill, bundled_skill, enabled_for, validate_traced_case
from app.services.ai.ai_gen_pipeline import (
    format_agent_done_message,
    iter_case_review,
    run_agent_pipeline,
    run_agent_pipeline_resume_cases,
)
from app.services.ai.case_review_dims import normalize_review_dims
from app.services.ai.effective_ai_settings import describe_key_resolution, get_llm_runtime
from app.services.knowledge.rag_retrieval import rag_search_hits, upload_document_ids_for_library
from app.services.functional.requirement_content import (
    requirement_assets_for_ai,
    requirement_text_for_ai,
    usable_requirement_text,
)
from app.services.functional.test_data_vars import parse_test_data_text
from app.services.functional.testcase_lifecycle import ensure_baseline_version
from app.tasks.knowledge.reindex_entity import enqueue_reindex
from app.user_messages import (
    LLM_MODEL_NOT_CONFIGURED,
    REQUIREMENT_NOT_IN_PROJECT,
    llm_model_invalid_detail,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/ai", tags=["ai"])

STEP_EXPECTED_PLACEHOLDER = "—"

RAG_TOP_K_FOR_GENERATE = 12


def _llm_model_allowlist(db: Session) -> set[str]:
    rt = get_llm_runtime(db)
    raw = rt.allowed_models_csv or ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    default = (rt.model or "").strip()
    if not default:
        return set(parts)
    if not parts:
        return {default}
    return set(parts) | {default}


def _resolve_generate_llm_model(db: Session, requested: str | None) -> str:
    allow = _llm_model_allowlist(db)
    if not allow:
        raise AppError("bad_request", LLM_MODEL_NOT_CONFIGURED, status_code=400)
    if requested is None or not str(requested).strip():
        rt = get_llm_runtime(db)
        return (rt.model or "").strip() or next(iter(sorted(allow)))
    name = str(requested).strip()
    if name not in allow:
        allowed_list = ", ".join(sorted(allow))
        raise AppError(
            "bad_request",
            llm_model_invalid_detail(allowed_list),
            status_code=400,
        )
    return name


def _validate_requirement_project(db: Session, project_id: int, requirement_id: int | None) -> None:
    if requirement_id is None:
        return
    r = db.get(Requirement, requirement_id)
    if r is None or r.project_id != project_id:
        raise AppError("bad_request", REQUIREMENT_NOT_IN_PROJECT, status_code=400)


def _parse_cases_from_llm(raw: str) -> list[GeneratedCase]:
    data = json.loads(raw)
    if isinstance(data, list):
        payload = data
    elif isinstance(data, dict) and "cases" in data:
        payload = data["cases"]
    else:
        raise ValueError("expected JSON array or object with key 'cases'")
    adapter = TypeAdapter(list[GeneratedCase])
    return adapter.validate_python(payload)


def _normalize_chat_messages(body: AiGenerateRequest) -> list[PromptMessage]:
    if body.messages:
        valid = [m for m in body.messages if m.content.strip()]
        if valid:
            return list(valid)
    if body.user_notes and body.user_notes.strip():
        return [ChatMessage(role="user", content=body.user_notes.strip())]
    if body.requirement_id is not None:
        return [ChatMessage(role="user", content="根据所选需求文档生成测试用例")]
    return [ChatMessage(role="user", content="根据项目知识库生成测试用例")]


def _truncate_log(text: str, max_len: int = 300) -> str:
    one_line = text.replace("\n", "\\n")
    if len(one_line) <= max_len:
        return one_line
    return f"{one_line[:max_len]}…"


def _load_requirement_for_generate(
    db: Session, project_id: int, requirement_id: int | None
) -> tuple[Requirement | None, list[tuple[str, bytes]]]:
    if requirement_id is None:
        return None, []
    r = (
        db.query(Requirement)
        .options(selectinload(Requirement.attachments))
        .filter(Requirement.id == requirement_id)
        .first()
    )
    if r is None or r.project_id != project_id:
        raise AppError("bad_request", REQUIREMENT_NOT_IN_PROJECT, status_code=400)
    _text, images = requirement_assets_for_ai(r)
    return r, images


def _content_with_images(
    text: str, images: list[tuple[str, bytes]] | None
) -> str | list[dict[str, Any]]:
    if not images:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for mime, blob in images:
        encoded = base64.standard_b64encode(blob).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }
        )
    return parts


def _inject_requirement_content(
    db: Session,
    project_id: int,
    body: AiGenerateRequest,
    chat_messages: list[PromptMessage],
) -> list[PromptMessage]:
    if body.requirement_id is None:
        return chat_messages
    r, _images = _load_requirement_for_generate(db, project_id, body.requirement_id)
    assert r is not None
    body_md = usable_requirement_text(requirement_text_for_ai(r)) or "(空)"
    req_block = f"需求标题: {r.title}\n需求正文:\n{body_md}"
    log.info(
        "用例生成注入需求 项目=%s 需求=%s 标题=%s 正文长度=%d",
        project_id,
        body.requirement_id,
        r.title,
        len(body_md),
    )
    if not chat_messages:
        return [
            PromptMessage(
                role="user",
                content=f"{req_block}\n\n请根据上述需求文档生成测试用例。",
            )
        ]
    first = chat_messages[0]
    if body.user_notes and body.user_notes.strip():
        content = f"{req_block}\n\n用户补充说明:\n{first.content}"
    elif first.content == "根据所选需求文档生成测试用例":
        content = f"{req_block}\n\n请根据上述需求文档生成测试用例。"
    else:
        content = f"{req_block}\n\n{first.content}"
    return [PromptMessage(role="user", content=content)] + list(chat_messages[1:])


def _last_user_message(chat_messages: list[PromptMessage]) -> str:
    for m in reversed(chat_messages):
        if m.role == "user":
            return m.content.strip()
    return chat_messages[-1].content.strip() if chat_messages else ""


def _build_retrieval_query(
    db: Session,
    project_id: int,
    body: AiGenerateRequest,
    chat_messages: list[PromptMessage],
) -> str:
    parts: list[str] = []
    last_user = _last_user_message(chat_messages)
    if last_user:
        parts.append(last_user)
    if body.requirement_id is not None:
        r, _images = _load_requirement_for_generate(db, project_id, body.requirement_id)
        assert r is not None
        body_md = usable_requirement_text(requirement_text_for_ai(r)) or "(空)"
        parts.append(f"需求标题: {r.title}\n需求正文:\n{body_md}")
    return "\n\n".join(parts) if parts else "根据项目知识库生成测试用例"


def _system_prompt() -> str:
    return (
        "你是测试工程师。根据用户说明、对话历史与检索到的项目片段，设计测试用例。"
        "若提供了当前用例草稿，且用户要求补充、修改或删除，请输出修订后的完整 cases 列表（不是增量）；"
        "未要求改动的条目保持原意。"
        "只输出一个 JSON 对象，格式为 {\"cases\": [...]}。"
        "每个元素含 title、overall_expected（整条用例的总体期望，必填）、"
        "module（所属功能模块，字符串，可省略）、"
        "case_type（功能、边界、异常、权限之一，无法归类时用功能）、"
        "precondition（执行前需满足的条件，没有则空字符串）、"
        "test_data（步骤要用的具体输入，写成键值文本，如账号、密码、金额、边界值；没有则空字符串）、"
        "priority（建议填写 P0、P1、P2、P3 或 P4 之一）、"
        "steps（数组，每项仅含 order、action，不要逐步 expected）。"
        "不要输出 Markdown 或其它说明文字。"
    )


def _drafts_block(current_drafts: list[GeneratedCase] | None) -> str | None:
    if not current_drafts:
        return None
    payload = [c.model_dump() for c in current_drafts]
    return "当前用例草稿（请按用户最新要求修订后，输出完整 cases 列表）：\n" + json.dumps(
        payload, ensure_ascii=False
    )


def _build_llm_messages(
    context: str,
    chat_messages: list[PromptMessage],
    current_drafts: list[GeneratedCase] | None = None,
    images: list[tuple[str, bytes]] | None = None,
    skill: FastCaseSkill | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": skill.prompt() if skill else _system_prompt()}]
    if not chat_messages:
        first_text = f"检索上下文：\n{context}\n\n请生成若干条高质量测试用例。"
    else:
        first = chat_messages[0]
        first_text = f"检索上下文：\n{context}\n\n{first.content}"
    messages.append({"role": "user", "content": _content_with_images(first_text, images)})
    if chat_messages:
        for m in chat_messages[1:]:
            messages.append({"role": m.role, "content": m.content})
    drafts_text = _drafts_block(current_drafts)
    if drafts_text:
        messages.append({"role": "user", "content": drafts_text})
    messages.append(
        {
            "role": "user",
            "content": '请输出 JSON 对象 {"cases":[...]}，不要 Markdown。',
        }
    )
    return messages


def _format_assistant_message(
    cases: list[GeneratedCase], *, revised: bool = False
) -> str:
    if not cases:
        return "未生成用例，请调整描述后重试。"
    titles = "、".join(c.title for c in cases[:5])
    if len(cases) > 5:
        titles = f"{titles} 等{len(cases)}条"
    verb = "已更新" if revised else "已生成"
    return f"{verb} {len(cases)} 条用例：{titles}"


def _sse_event(event_type: str, data: dict) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _cases_from_llm_raw(
    raw: str,
    messages: list[dict[str, Any]],
    *,
    use_llm_model: str,
    llm_cfg,
    skill: FastCaseSkill | None = None,
    previous: list[GeneratedCase] | None = None,
) -> list[GeneratedCase]:
    def parse(text: str) -> list[GeneratedCase]:
        cases = _parse_cases_from_llm(text)
        return (
            skill.complete_cases(cases, previous, llm_model=use_llm_model)
            if skill
            else cases
        )

    try:
        return parse(raw)
    except Exception as e1:
        log.warning(
            "用例生成大模型 JSON 解析失败，正在重试: %s 预览=%s",
            e1,
            (raw[:300] + "…") if len(raw) > 300 else raw,
        )
        retry_messages = list(messages)
        retry_messages.append({"role": "assistant", "content": raw})
        retry_messages.append(
            {
                "role": "user",
                "content": f"上次输出无法解析为合法 JSON（错误: {e1}）。请仅输出合法 JSON 对象，键为 cases。",
            }
        )
        raw2 = llm.chat_complete(
            retry_messages, model=use_llm_model, db=None, cfg=llm_cfg, scene="用例生成"
        )
        try:
            return parse(raw2)
        except Exception as e2:
            raise AppError(
                "unprocessable_entity",
                f"模型修订后仍未满足用例字段要求：{str(e2)[:240]}" if skill else "LLM 返回内容无法解析为测试用例 JSON",
                detail=str(e2),
                status_code=422,
            ) from e2


def _validate_generate_request(
    db: Session,
    project_id: int,
    body: AiGenerateRequest,
) -> tuple[str, list[PromptMessage], list[tuple[str, bytes]]]:
    rt = get_llm_runtime(db)
    has_key = bool(rt.api_key.strip())
    key_diag = describe_key_resolution(db)
    raw_msg_count = len(body.messages) if body.messages else 0
    mode_label = "分步" if body.generate_mode == "agent" else "快速"
    log.info(
        "用例生成开始 项目=%s 需求=%s 知识库=%s 模式=%s 消息数=%d 请求模型=%s 已配置密钥=%s API地址=%s 密钥诊断=%s",
        project_id,
        body.requirement_id,
        body.knowledge_library_id,
        mode_label,
        raw_msg_count,
        body.llm_model,
        has_key,
        rt.api_base,
        key_diag,
    )
    if not has_key:
        raise AppError(
            "bad_request",
            "未配置 LLM API Key，请在系统 AI 配置或 .env 中设置",
            status_code=400,
        )
    use_llm_model = _resolve_generate_llm_model(db, body.llm_model)
    _req, images = _load_requirement_for_generate(db, project_id, body.requirement_id)
    chat_messages = _normalize_chat_messages(body)
    chat_messages = _inject_requirement_content(db, project_id, body, chat_messages)
    user_preview = _truncate_log(_last_user_message(chat_messages), 200)
    log.info(
        "用例生成用户消息 项目=%s 长度=%d 预览=%s 附图=%d",
        project_id,
        len(_last_user_message(chat_messages)),
        user_preview,
        len(images),
    )
    return use_llm_model, chat_messages, images


def _fetch_rag_context(
    db: Session,
    project_id: int,
    body: AiGenerateRequest,
    chat_messages: list[PromptMessage],
) -> tuple[str, list]:
    query = _build_retrieval_query(db, project_id, body, chat_messages)
    upload_filter: list[int] | None = None
    hits = []
    if body.knowledge_library_id is not None:
        upload_filter = upload_document_ids_for_library(
            db, project_id, body.knowledge_library_id
        )
        hits = rag_search_hits(
            db,
            project_id,
            query,
            RAG_TOP_K_FOR_GENERATE,
            upload_document_ids=upload_filter,
        )
    query_preview = (query[:200] + "…") if len(query) > 200 else query
    log.info(
        "用例生成检索 项目=%s 命中=%d 文档过滤=%s 查询长度=%d 查询预览=%s",
        project_id,
        len(hits),
        upload_filter if upload_filter is not None else "未使用",
        len(query),
        query_preview,
    )
    context_blocks = []
    for h in hits:
        context_blocks.append(f"[{h.title_resolved}]\n{h.text}")
    context = "\n\n---\n\n".join(context_blocks) if context_blocks else "(无检索片段)"
    return context, hits


def _iter_fast_generate_stream_events(
    *,
    project_id: int,
    context: str,
    chat_messages: list[PromptMessage],
    use_llm_model: str,
    llm_cfg,
    current_drafts: list[GeneratedCase] | None = None,
    enable_case_review: bool = False,
    review_dimensions: list | None = None,
    images: list[tuple[str, bytes]] | None = None,
    skill: FastCaseSkill | None = None,
) -> Iterator[str]:
    messages = _build_llm_messages(context, chat_messages, current_drafts, images=images, skill=skill)
    log.info(
        "用例生成调用大模型 模式=快速 模型=%s 上下文长度=%d 对话轮数=%d 消息数=%d",
        use_llm_model,
        len(context),
        len(chat_messages),
        len(messages),
    )
    yield _sse_event("status", {"message": "正在生成用例…"})

    raw = ""
    try:
        for delta in llm.chat_complete_stream(
            messages, model=use_llm_model, cfg=llm_cfg, scene="用例生成"
        ):
            raw += delta
            yield _sse_event("delta", {"content": delta})
    except Exception:
        log.exception(
            "用例生成大模型流式调用失败 项目=%s 模型=%s 上下文长度=%d",
            project_id,
            use_llm_model,
            len(context),
        )
        raise

    raw_stripped = raw.strip()
    if not raw_stripped:
        log.warning(
            "用例生成大模型返回空响应 项目=%s 模型=%s 上下文长度=%d",
            project_id,
            use_llm_model,
            len(context),
        )

    cases = _cases_from_llm_raw(
        raw,
        messages,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        skill=skill, previous=current_drafts,
    )
    dims = normalize_review_dims(review_dimensions, enable=enable_case_review)
    review_result = None
    if dims:
        for event in iter_case_review(
            draft_cases=cases,
            dims=dims,
            use_llm_model=use_llm_model,
            llm_cfg=llm_cfg,
            context=context,
            skill=skill,
        ):
            event_type = event.get("type")
            if event_type == "review_done":
                cases = event["cases"]
                review_result = event.get("review")
                continue
            payload = {k: v for k, v in event.items() if k != "type"}
            yield _sse_event(str(event_type), payload)

    if review_result is not None:
        assistant_message = format_agent_done_message(cases, review_result)
    else:
        assistant_message = _format_assistant_message(
            cases, revised=bool(current_drafts)
        )
    if cases:
        log.info("用例生成完成 项目=%s 用例数=%d", project_id, len(cases))
    else:
        log.warning(
            "用例生成结果为空 项目=%s 模型=%s 原始响应长度=%d 预览=%s",
            project_id,
            use_llm_model,
            len(raw_stripped),
            _truncate_log(raw_stripped, 500) if raw_stripped else "(空)",
        )
    yield _sse_event(
        "done",
        {
            "cases": [c.model_dump() for c in cases],
            "assistant_message": assistant_message,
        },
    )


def _agent_stream_events(
    *,
    context: str,
    chat_messages: list[PromptMessage],
    body: AiGenerateRequest,
    use_llm_model: str,
    llm_cfg,
    images: list[tuple[str, bytes]] | None = None,
    skill: FastCaseSkill | None = None,
) -> Iterator[dict[str, Any]]:
    if body.agent_resume_from == "cases":
        if body.agent_artifacts is None:
            raise AppError("bad_request", "续跑分步生成需携带 agent_artifacts", status_code=400)
        yield from run_agent_pipeline_resume_cases(
            context=context,
            artifacts_payload=body.agent_artifacts.model_dump(),
            use_llm_model=use_llm_model,
            llm_cfg=llm_cfg,
            enable_case_review=body.enable_case_review,
            review_dimensions=body.review_dimensions,
            skill=skill,
        )
        return
    yield from run_agent_pipeline(
        context=context,
        chat_messages=chat_messages,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        images=images,
        skill=skill,
    )


def _iter_agent_generate_stream_events(
    *,
    project_id: int,
    context: str,
    chat_messages: list[PromptMessage],
    body: AiGenerateRequest,
    use_llm_model: str,
    llm_cfg,
    images: list[tuple[str, bytes]] | None = None,
    skill: FastCaseSkill | None = None,
) -> Iterator[str]:
    resume_label = "续跑用例" if body.agent_resume_from == "cases" else "完整流程"
    log.info(
        "用例生成调用大模型 模式=分步(%s) 项目=%s 模型=%s 上下文长度=%d 对话轮数=%d 附图=%d",
        resume_label,
        project_id,
        use_llm_model,
        len(context),
        len(chat_messages),
        len(images or []),
    )
    for event in _agent_stream_events(
        context=context,
        chat_messages=chat_messages,
        body=body,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        images=images,
        skill=skill,
    ):
        event_type = event.pop("type")
        yield _sse_event(event_type, event)
        if event_type in ("clarification", "await_test_points_confirm", "done"):
            if event_type == "done":
                cases = event.get("cases") or []
                log.info("用例生成完成(分步) 项目=%s 用例数=%d", project_id, len(cases))
            return


def _iter_generate_stream_events(
    *,
    project_id: int,
    body: AiGenerateRequest,
    chat_messages: list[PromptMessage],
    use_llm_model: str,
    llm_cfg,
    images: list[tuple[str, bytes]] | None = None,
) -> Iterator[str]:
    yield _sse_event("status", {"message": "等待中…"})
    db = SessionLocal()
    try:
        if body.knowledge_library_id is not None:
            yield _sse_event("status", {"message": "正在检索知识库…"})
        context, _hits = _fetch_rag_context(db, project_id, body, chat_messages)
        skill = FastCaseSkill.for_request(db, project_id, body, _hits, images)

        if body.generate_mode == "agent":
            yield from _iter_agent_generate_stream_events(
                project_id=project_id,
                context=context,
                chat_messages=chat_messages,
                body=body,
                use_llm_model=use_llm_model,
                llm_cfg=llm_cfg,
                images=images,
                skill=skill,
            )
        else:
            yield from _iter_fast_generate_stream_events(
                skill=skill,
                project_id=project_id,
                context=context,
                chat_messages=chat_messages,
                use_llm_model=use_llm_model,
                llm_cfg=llm_cfg,
                current_drafts=body.current_drafts,
                enable_case_review=body.enable_case_review,
                review_dimensions=body.review_dimensions,
                images=images,
            )
    except AppError as e:
        yield _sse_event("error", {"message": e.message})
    except Exception as e:
        log.exception("用例生成流式失败 项目=%s", project_id)
        yield _sse_event("error", {"message": str(e)})
    finally:
        db.close()


@router.get("/llm-options", response_model=LlmOptionsOut)
def llm_options(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> LlmOptionsOut:
    allow = _llm_model_allowlist(db)
    models = sorted(allow)
    rt = get_llm_runtime(db)
    default = (rt.model or "").strip()
    if default not in allow and models:
        default = models[0]
    return LlmOptionsOut(default_model=default or (models[0] if models else ""), models=models,
                         case_design_skill=bundled_skill()[0] if enabled_for(project_id) else None)


@router.post("/generate-testcases/stream")
def generate_testcases_stream(
    project_id: int,
    body: AiGenerateRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> StreamingResponse:
    use_llm_model, chat_messages, images = _validate_generate_request(db, project_id, body)
    llm_cfg = get_llm_runtime(db)
    return StreamingResponse(
        _iter_generate_stream_events(
            project_id=project_id,
            body=body,
            chat_messages=chat_messages,
            use_llm_model=use_llm_model,
            llm_cfg=llm_cfg,
            images=images,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/generate-testcases", response_model=AiGenerateResponse)
def generate_testcases(
    project_id: int,
    body: AiGenerateRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AiGenerateResponse:
    use_llm_model, chat_messages, images = _validate_generate_request(db, project_id, body)
    llm_cfg = get_llm_runtime(db)
    context, _hits = _fetch_rag_context(db, project_id, body, chat_messages)
    skill = FastCaseSkill.for_request(db, project_id, body, _hits, images)

    if body.generate_mode == "agent":
        for event in _agent_stream_events(
            context=context,
            chat_messages=chat_messages,
            body=body,
            use_llm_model=use_llm_model,
            llm_cfg=llm_cfg,
            images=images,
            skill=skill,
        ):
            event_type = event.get("type")
            if event_type == "clarification":
                return AiGenerateResponse(
                    cases=[],
                    assistant_message=str(event.get("assistant_message") or "需求尚不明确，请补充信息"),
                )
            if event_type == "await_test_points_confirm":
                return AiGenerateResponse(
                    cases=[],
                    assistant_message=str(
                        event.get("assistant_message") or "已生成测试点，请确认后继续生成用例"
                    ),
                )
            if event_type == "done":
                cases_raw = event.get("cases") or []
                cases = TypeAdapter(list[GeneratedCase]).validate_python(cases_raw)
                assistant_message = str(event.get("assistant_message") or _format_assistant_message(cases))
                log.info("用例生成完成(分步) 项目=%s 用例数=%d", project_id, len(cases))
                return AiGenerateResponse(cases=cases, assistant_message=assistant_message)
        return AiGenerateResponse(cases=[], assistant_message="分步流程未产出用例")

    messages = _build_llm_messages(context, chat_messages, body.current_drafts, images=images, skill=skill)
    log.info(
        "用例生成调用大模型 模式=快速 模型=%s 上下文长度=%d 对话轮数=%d 消息数=%d 附图=%d",
        use_llm_model,
        len(context),
        len(chat_messages),
        len(messages),
        len(images),
    )
    raw = llm.chat_complete(messages, model=use_llm_model, cfg=llm_cfg, scene="用例生成")
    cases = _cases_from_llm_raw(
        raw,
        messages,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        skill=skill, previous=body.current_drafts,
    )
    dims = normalize_review_dims(body.review_dimensions, enable=body.enable_case_review)
    review_result = None
    if dims:
        for event in iter_case_review(
            draft_cases=cases,
            dims=dims,
            use_llm_model=use_llm_model,
            llm_cfg=llm_cfg,
            context=context,
            skill=skill,
        ):
            if event.get("type") == "review_done":
                cases = event["cases"]
                review_result = event.get("review")
                break
    if review_result is not None:
        assistant_message = format_agent_done_message(cases, review_result)
    else:
        assistant_message = _format_assistant_message(
            cases, revised=bool(body.current_drafts)
        )
    log.info("用例生成完成 项目=%s 用例数=%d", project_id, len(cases))
    return AiGenerateResponse(cases=cases, assistant_message=assistant_message)


def _confirmed_draft(db: Session, candidate: TestCase, *, lock: bool = False) -> TestCase | None:
    q = db.query(TestCase).filter_by(project_id=candidate.project_id, ai_draft_id=candidate.ai_draft_id)
    existing = (q.with_for_update() if lock else q).first()
    if existing is not None:
        fields = ("title", "requirement_id", "module", "precondition", "test_data", "priority",
                  "case_type", "overall_expected", "design_trace")
        def steps(row: TestCase):
            return sorted((s.order, s.action, s.expected) for s in row.steps)
        if any(getattr(existing, k) != getattr(candidate, k) for k in fields) or steps(existing) != steps(candidate):
            raise AppError("conflict", f"这条草稿已写入用例 #{existing.id}，请在用例库编辑已写入的内容", status_code=409)
    return existing


@router.post("/confirm-testcases", response_model=list[TestCaseOut], status_code=201)
def confirm_testcases(
    project_id: int,
    body: AiConfirmRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> list[TestCaseOut]:
    _validate_requirement_project(db, project_id, body.requirement_id)
    for gc in body.cases:
        if gc.draft_id or gc.design_trace:
            try:
                validate_traced_case(gc)
            except ValueError as exc:
                raise AppError("bad_request", str(exc), status_code=422) from exc
            trace = gc.design_trace
            if not gc.draft_id or not trace.skill_version or not trace.source_digest:
                raise AppError("bad_request", "设计草稿缺少来源版本或草稿编号", status_code=422)
            _validate_requirement_project(db, project_id, trace.requirement_id)
    result_ids: list[int] = []
    new_ids: list[int] = []
    for gc in body.cases:
        td = clean_precondition(gc.test_data)
        parsed_vars = parse_test_data_text(td)
        c = TestCase(
            project_id=project_id,
            requirement_id=gc.design_trace.requirement_id if gc.design_trace else body.requirement_id,
            ai_draft_id=gc.draft_id,
            design_trace=gc.design_trace.model_dump() if gc.design_trace else None,
            title=gc.title,
            module=(gc.module or "").strip() or None,
            precondition=clean_precondition(gc.precondition),
            test_data=td,
            variables_json=parsed_vars or None,
            case_type=clean_case_type(gc.case_type),
            overall_expected=(gc.overall_expected or "").strip(),
            priority=gc.priority,
            status="draft",
            lifecycle_status="draft",
            tags_json=None,
            creator_id=user.id,
        )
        for st in gc.steps:
            step_expected = (st.expected or "").strip() or STEP_EXPECTED_PLACEHOLDER
            c.steps.append(
                TestCaseStep(
                    order=st.order,
                    action=st.action,
                    expected=step_expected,
                )
            )
        if gc.draft_id:
            existing = _confirmed_draft(db, c)
            if existing is not None:
                result_ids.append(existing.id)
                continue
            try:
                with db.begin_nested():
                    db.add(c)
                    db.flush()
            except IntegrityError:
                existing = _confirmed_draft(db, c, lock=True)
                if existing is None:
                    raise
                result_ids.append(existing.id)
                continue
        else:
            db.add(c)
            db.flush()
        # AI drafts start their version history at v1 so later edits are traceable.
        ensure_baseline_version(db, c, source="ai_confirmed", actor_id=user.id)
        result_ids.append(c.id)
        new_ids.append(c.id)
    db.commit()
    for cid in new_ids:
        enqueue_reindex(project_id, "case", cid)
    out: list[TestCaseOut] = []
    for cid in result_ids:
        c = (
            db.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id == cid, TestCase.project_id == project_id)
            .one()
        )
        out.append(_case_to_out(c))
    return out
