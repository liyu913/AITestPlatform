import base64
import json
import logging
from collections.abc import Iterator
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter

from app.errors import AppError
from app.schemas.ai.ai_agent import (
    AgentArtifacts as PipelineArtifacts,
    CaseReviewOut,
    CaseReviewResult,
    RequirementAnalysisOut,
    TestPointsResponse,
)
from app.schemas.ai.ai_gen import ChatMessage, GeneratedCase, ReviewDimensionConfig
from app.services.ai import llm
from app.services.ai.case_review_dims import (
    build_review_checklist_text,
    issue_type_keys_csv,
    normalize_review_dims,
)
from app.services.ai.effective_ai_settings import LlmRuntime

log = logging.getLogger(__name__)

_PHASE_LABELS = {
    "requirement_analysis": "需求分析",
    "test_points": "测试点",
    "cases": "用例生成",
    "review": "用例检查",
    "unknown": "未知",
}

T = TypeVar("T", bound=BaseModel)


def _phase_label(phase: str) -> str:
    return _PHASE_LABELS.get(phase, phase)


def _format_chat_history(chat_messages: list[ChatMessage]) -> str:
    if not chat_messages:
        return "(无对话)"
    lines: list[str] = []
    for m in chat_messages:
        role = "用户" if m.role == "user" else "助手"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


def _parse_json_model(raw: str, adapter: TypeAdapter[T]) -> T:
    data = json.loads(raw)
    if isinstance(data, dict) and len(data) == 1:
        only_val = next(iter(data.values()))
        if isinstance(only_val, dict):
            data = only_val
    return adapter.validate_python(data)


def _json_complete_with_retry(
    messages: list[dict[str, str]],
    *,
    adapter: TypeAdapter[T],
    use_llm_model: str,
    llm_cfg: LlmRuntime,
    retry_user_hint: str,
    phase: str = "unknown",
) -> T:
    log.info(
        "分步生成调用大模型 阶段=%s 模型=%s 消息数=%d",
        _phase_label(phase),
        use_llm_model,
        len(messages),
    )
    scene = f"用例生成-{_phase_label(phase)}"
    raw = llm.chat_complete(messages, model=use_llm_model, db=None, cfg=llm_cfg, scene=scene)
    try:
        return _parse_json_model(raw, adapter)
    except Exception as e1:
        log.warning("分步生成 JSON 解析失败 阶段=%s 正在重试: %s", _phase_label(phase), e1)
        retry_messages = list(messages)
        retry_messages.append({"role": "assistant", "content": raw})
        retry_messages.append(
            {
                "role": "user",
                "content": f"{retry_user_hint}（解析错误: {e1}）",
            }
        )
        log.info(
            "分步生成大模型重试 阶段=%s 模型=%s 消息数=%d",
            _phase_label(phase),
            use_llm_model,
            len(retry_messages),
        )
        raw2 = llm.chat_complete(
            retry_messages, model=use_llm_model, db=None, cfg=llm_cfg, scene=scene
        )
        try:
            return _parse_json_model(raw2, adapter)
        except Exception as e2:
            raise AppError(
                "unprocessable_entity",
                "Agent 阶段输出无法解析为合法 JSON",
                detail=str(e2),
                status_code=422,
            ) from e2


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


def _cases_from_llm_raw(
    raw: str,
    messages: list[dict[str, str]],
    *,
    use_llm_model: str,
    llm_cfg: LlmRuntime,
) -> list[GeneratedCase]:
    try:
        return _parse_cases_from_llm(raw)
    except Exception as e1:
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
            return _parse_cases_from_llm(raw2)
        except Exception as e2:
            raise AppError(
                "unprocessable_entity",
                "LLM 返回内容无法解析为测试用例 JSON",
                detail=str(e2),
                status_code=422,
            ) from e2


def _requirement_analysis_prompt() -> str:
    return (
        "你是资深测试分析师。根据检索上下文与用户对话，分析测试需求是否足够清晰以设计用例。"
        "只输出 JSON 对象，字段："
        "is_clear（布尔，需求是否已足够明确）、"
        "summary（字符串，需求摘要；is_clear 为 true 时必填）、"
        "ambiguities（字符串数组，不明确点）、"
        "questions（字符串数组，需用户回答的具体问题；is_clear 为 false 时至少 1 条）。"
        "不要输出 Markdown。"
    )


def _test_points_prompt() -> str:
    return (
        "你是测试设计专家。根据需求摘要与上下文，生成覆盖矩阵式测试点。"
        "只输出 JSON 对象 {\"test_points\": [...]}，每项含 "
        "id（如 TP-01）、title、category（功能/边界/异常/权限等）、"
        "priority_hint（P0-P4 可选）。"
        "不要输出 Markdown。"
    )


def _cases_from_points_prompt() -> str:
    return (
        "你是测试工程师。根据测试点生成测试用例。"
        "只输出 JSON 对象 {\"cases\": [...]}。"
        "每个测试点至少 1 条用例；每条含 title、overall_expected（必填）、"
        "module（可省略）、priority（P0-P4）、"
        "case_type（与该测试点的 category 一致，只能是功能、边界、异常、权限，对不上时用功能）、"
        "precondition（执行前需满足的条件，没有则空字符串）、"
        "test_data（步骤要用的具体输入，写成键值文本，如账号、密码、金额、边界值；没有则空字符串）、"
        "steps（每项仅 order、action，不要逐步 expected）。"
        "不要输出 Markdown。"
    )


def _review_prompt(dims: list[ReviewDimensionConfig]) -> str:
    checklist = build_review_checklist_text(dims)
    keys = issue_type_keys_csv(dims)
    return (
        "你是测试用例评审专家。请严格按下列维度（权重越高越优先）检查并修订用例：\n"
        f"{checklist}\n"
        "只输出 JSON 对象，字段："
        "summary（检查结论摘要）、"
        "issues（数组，每项含 case_title、issue_type、detail；"
        f"issue_type 只能是：{keys}）、"
        "dimension_scores（对象，键为上述维度 key，值为 1–5 整数评分）、"
        "cases（修订后的最终用例数组，格式与输入 cases 相同）。"
        "可合并冗余、删除不合理、微调标题/步骤/优先级。"
        "修订后的 cases 保留 case_type、precondition 与 test_data。"
        "不要输出 Markdown。"
    )


def format_clarification_message(analysis: RequirementAnalysisOut) -> str:
    if not analysis.questions:
        amb = "、".join(analysis.ambiguities) if analysis.ambiguities else "需求描述不够具体"
        return f"需求尚不明确（{amb}），请补充更多信息。"
    lines = "\n".join(f"- {q}" for q in analysis.questions)
    return f"需求尚不明确，请补充以下信息：\n{lines}"


def _artifacts_from_resume_payload(artifacts: PipelineArtifacts | dict[str, Any]) -> tuple[RequirementAnalysisOut, list]:
    if isinstance(artifacts, PipelineArtifacts):
        data = artifacts
    else:
        data = PipelineArtifacts.model_validate(artifacts)
    if data.requirement_analysis is None:
        raise AppError("bad_request", "缺少需求分析结果，无法继续生成用例", status_code=400)
    if not data.test_points:
        raise AppError("bad_request", "缺少测试点，无法继续生成用例", status_code=400)
    return data.requirement_analysis, data.test_points


def iter_case_review(
    *,
    draft_cases: list[GeneratedCase],
    dims: list[ReviewDimensionConfig],
    use_llm_model: str,
    llm_cfg: LlmRuntime,
    test_points_json: str | None = None,
    context: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield phase/status/review events; final event is type=review_done with cases+result."""
    if not dims:
        yield {
            "type": "review_done",
            "cases": draft_cases,
            "review": None,
        }
        return

    log.info("用例检查 状态=开始 模型=%s 维度数=%d", use_llm_model, len(dims))
    yield {"type": "phase", "phase": "review", "status": "start"}
    yield {"type": "status", "message": "正在按多维度评审用例…"}
    cases_json = json.dumps([c.model_dump() for c in draft_cases], ensure_ascii=False)
    context_parts: list[str] = []
    if context and context.strip() and context.strip() != "(无检索片段)":
        context_parts.append(f"检索上下文：\n{context.strip()}")
    if test_points_json:
        context_parts.append(f"测试点：\n{test_points_json}")
    context_parts.append(f"待检查用例：\n{cases_json}")
    context_parts.append("请按维度评审并输出修订后的 cases。")
    review_messages = [
        {"role": "system", "content": _review_prompt(dims)},
        {"role": "user", "content": "\n\n".join(context_parts)},
    ]
    review_out = _json_complete_with_retry(
        review_messages,
        adapter=TypeAdapter(CaseReviewOut),
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        retry_user_hint="请输出含 summary、issues、dimension_scores、cases 的合法 JSON 对象",
        phase="review",
    )
    final_cases = review_out.cases if review_out.cases else draft_cases
    review_result = CaseReviewResult(
        summary=review_out.summary,
        issues=review_out.issues,
        dimension_scores=review_out.dimension_scores,
    )
    yield {
        "type": "review",
        "summary": review_result.summary,
        "issues": [i.model_dump() for i in review_result.issues],
        "dimension_scores": review_result.dimension_scores,
    }
    log.info("用例检查 状态=完成 最终用例数=%d", len(final_cases))
    yield {"type": "phase", "phase": "review", "status": "done"}
    yield {
        "type": "review_done",
        "cases": final_cases,
        "review": review_result,
    }


def _iter_cases_and_review(
    *,
    context: str,
    analysis: RequirementAnalysisOut,
    test_points: list,
    artifacts: PipelineArtifacts,
    use_llm_model: str,
    llm_cfg: LlmRuntime,
    enable_case_review: bool = False,
    review_dimensions: list[ReviewDimensionConfig] | None = None,
) -> Iterator[dict[str, Any]]:
    log.info("分步生成流水线 阶段=用例生成 状态=开始 模型=%s", use_llm_model)
    yield {"type": "phase", "phase": "cases", "status": "start"}
    yield {"type": "status", "message": "正在生成用例…"}
    tp_json = json.dumps([tp.model_dump() for tp in test_points], ensure_ascii=False)
    case_messages = [
        {"role": "system", "content": _cases_from_points_prompt()},
        {
            "role": "user",
            "content": (
                f"检索上下文：\n{context}\n\n"
                f"需求摘要：\n{analysis.summary}\n\n"
                f"测试点：\n{tp_json}\n\n"
                "请为每个测试点生成至少一条用例。"
            ),
        },
        {
            "role": "user",
            "content": '请输出 JSON 对象 {"cases":[...]}，不要 Markdown。',
        },
    ]
    raw_cases = ""
    for delta in llm.chat_complete_stream(
        case_messages, model=use_llm_model, cfg=llm_cfg, scene="用例生成"
    ):
        raw_cases += delta
        yield {"type": "delta", "content": delta}

    draft_cases = _cases_from_llm_raw(
        raw_cases,
        case_messages,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
    )
    log.info("分步生成流水线 阶段=用例生成 状态=完成 草稿数=%d", len(draft_cases))
    yield {"type": "phase", "phase": "cases", "status": "done"}

    dims = normalize_review_dims(review_dimensions, enable=enable_case_review)
    final_cases = draft_cases
    review_result: CaseReviewResult | None = None
    for event in iter_case_review(
        draft_cases=draft_cases,
        dims=dims,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        test_points_json=tp_json,
        context=context,
    ):
        if event.get("type") == "review_done":
            final_cases = event["cases"]
            review_result = event.get("review")
            continue
        yield event

    if review_result is not None:
        artifacts.review = review_result

    assistant_message = format_agent_done_message(final_cases, review_result)
    yield {
        "type": "done",
        "cases": [c.model_dump() for c in final_cases],
        "assistant_message": assistant_message,
        "artifacts": artifacts.model_dump(),
    }


def run_agent_pipeline_resume_cases(
    *,
    context: str,
    artifacts_payload: PipelineArtifacts | dict[str, Any],
    use_llm_model: str,
    llm_cfg: LlmRuntime,
    enable_case_review: bool = False,
    review_dimensions: list[ReviewDimensionConfig] | None = None,
) -> Iterator[dict[str, Any]]:
    analysis, test_points = _artifacts_from_resume_payload(artifacts_payload)
    if isinstance(artifacts_payload, PipelineArtifacts):
        artifacts = artifacts_payload
    else:
        artifacts = PipelineArtifacts.model_validate(artifacts_payload)
    yield from _iter_cases_and_review(
        context=context,
        analysis=analysis,
        test_points=test_points,
        artifacts=artifacts,
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        enable_case_review=enable_case_review,
        review_dimensions=review_dimensions,
    )


def format_agent_done_message(
    cases: list[GeneratedCase],
    review: CaseReviewResult | None,
) -> str:
    if not cases:
        return "未生成用例，请调整描述后重试。"
    titles = "、".join(c.title for c in cases[:5])
    if len(cases) > 5:
        titles = f"{titles} 等{len(cases)}条"
    if review is not None:
        base = f"已生成 {len(cases)} 条用例（多维评审完成）：{titles}"
        if review.summary.strip():
            return f"{base}\n\n检查报告：{review.summary.strip()}"
        return base
    return f"已生成 {len(cases)} 条用例：{titles}"


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


def run_agent_pipeline(
    *,
    context: str,
    chat_messages: list[ChatMessage],
    use_llm_model: str,
    llm_cfg: LlmRuntime,
    images: list[tuple[str, bytes]] | None = None,
) -> Iterator[dict[str, Any]]:
    chat_text = _format_chat_history(chat_messages)
    artifacts = PipelineArtifacts()

    # Phase 1: requirement analysis
    log.info(
        "分步生成流水线 阶段=需求分析 状态=开始 模型=%s 附图=%d",
        use_llm_model,
        len(images or []),
    )
    yield {"type": "phase", "phase": "requirement_analysis", "status": "start"}
    yield {"type": "status", "message": "正在分析需求…"}
    analysis_user = (
        f"检索上下文：\n{context}\n\n"
        f"对话历史：\n{chat_text}\n\n"
        "请分析需求是否足够清晰以生成测试用例。"
    )
    analysis_messages = [
        {"role": "system", "content": _requirement_analysis_prompt()},
        {
            "role": "user",
            "content": _content_with_images(analysis_user, images),
        },
    ]
    analysis = _json_complete_with_retry(
        analysis_messages,
        adapter=TypeAdapter(RequirementAnalysisOut),
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        retry_user_hint="请仅输出合法 JSON，含 is_clear、summary、ambiguities、questions",
        phase="requirement_analysis",
    )
    artifacts.requirement_analysis = analysis
    log.info("分步生成流水线 阶段=需求分析 状态=完成 需求清晰=%s", analysis.is_clear)
    yield {"type": "phase", "phase": "requirement_analysis", "status": "done"}

    if not analysis.is_clear:
        assistant_message = format_clarification_message(analysis)
        yield {
            "type": "clarification",
            "analysis": analysis.model_dump(),
            "questions": analysis.questions,
            "assistant_message": assistant_message,
            "artifacts": artifacts.model_dump(),
        }
        return

    yield {
        "type": "analysis_done",
        "summary": analysis.summary,
        "artifacts": artifacts.model_dump(),
    }

    # Phase 2: test points
    log.info("分步生成流水线 阶段=测试点 状态=开始 模型=%s", use_llm_model)
    yield {"type": "phase", "phase": "test_points", "status": "start"}
    yield {"type": "status", "message": "正在生成测试点…"}
    tp_messages = [
        {"role": "system", "content": _test_points_prompt()},
        {
            "role": "user",
            "content": (
                f"检索上下文：\n{context}\n\n"
                f"需求摘要：\n{analysis.summary}\n\n"
                f"对话历史：\n{chat_text}\n\n"
                "请生成测试点列表。"
            ),
        },
    ]
    tp_resp = _json_complete_with_retry(
        tp_messages,
        adapter=TypeAdapter(TestPointsResponse),
        use_llm_model=use_llm_model,
        llm_cfg=llm_cfg,
        retry_user_hint='请仅输出合法 JSON 对象 {"test_points":[...]}',
        phase="test_points",
    )
    test_points = tp_resp.test_points
    artifacts.test_points = test_points
    yield {
        "type": "test_points",
        "items": [tp.model_dump() for tp in test_points],
    }
    log.info("分步生成流水线 阶段=测试点 状态=完成 数量=%d", len(test_points))
    yield {"type": "phase", "phase": "test_points", "status": "done"}

    n = len(test_points)
    assistant_message = f"已生成 {n} 个测试点，请确认后继续生成用例。"
    yield {
        "type": "await_test_points_confirm",
        "items": [tp.model_dump() for tp in test_points],
        "assistant_message": assistant_message,
        "artifacts": artifacts.model_dump(),
    }
