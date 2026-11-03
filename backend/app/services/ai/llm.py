import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.errors import AppError
from app.user_messages import LLM_RUNTIME_NOT_CONFIGURED
from app.services.ai.ai_http_errors import check_ai_http_response
from app.services.ai.call_log import format_error, format_messages, record_ai_call, token_fields
from app.services.ai.effective_ai_settings import LlmRuntime, get_llm_runtime

log = logging.getLogger(__name__)


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", "\\n")
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…"


_ROLE_LABELS = {
    "system": "系统",
    "user": "用户",
    "assistant": "助手",
}


def _content_preview(content: Any, max_len: int) -> str:
    if isinstance(content, list):
        texts: list[str] = []
        images = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                images += 1
                continue
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
            else:
                texts.append(str(part))
        text = " ".join(texts)
        image_note = f" 图片={images}" if images else ""
        return f"长度={len(text)}{image_note} {_truncate(text, max_len)}"
    text = str(content or "")
    return f"长度={len(text)} {_truncate(text, max_len)}"


def _preview_messages(messages: list[dict[str, Any]], max_len: int = 120) -> str:
    parts: list[str] = []
    for i, m in enumerate(messages):
        role = _ROLE_LABELS.get(str(m.get("role", "?")), str(m.get("role", "?")))
        parts.append(f"[{i}] {role} {_content_preview(m.get('content'), max_len)}")
    return " | ".join(parts)


def _log_usage(data: dict[str, Any], *, use_model: str, prefix: str) -> None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return
    log.info(
        "%s token 用量 模型=%s 提示=%s 补全=%s 合计=%s",
        prefix,
        use_model,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )


def chat_complete(
    messages: list[dict[str, Any]],
    *,
    db: Session | None = None,
    cfg: LlmRuntime | None = None,
    temperature: float = 0.2,
    model: str | None = None,
    json_mode: bool = True,
    scene: str | None = None,
) -> str:
    runtime = _resolve_llm_cfg(db=db, cfg=cfg)
    use_model = model if model is not None else runtime.model
    if not runtime.api_key.strip():
        record_ai_call(
            kind="chat",
            scene=scene,
            model=use_model,
            status="error",
            http_status=None,
            latency_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            error_message="未配置 LLM API Key，请在系统 AI 配置或 .env 中设置",
            request_text=format_messages(messages),
            response_text=None,
        )
        raise AppError(
            "bad_request",
            "未配置 LLM API Key，请在系统 AI 配置或 .env 中设置",
            status_code=400,
        )
    url = f"{runtime.api_base.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    log.info(
        "大模型同步请求 模型=%s 地址=%s 温度=%s 流式=false 消息数=%d 预览=%s",
        use_model,
        url,
        temperature,
        len(messages),
        _preview_messages(messages),
    )
    started = time.perf_counter()
    status_code: int | None = None
    content = ""
    prompt_tokens = completion_tokens = total_tokens = None
    error: BaseException | None = None
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            status_code = r.status_code
            check_ai_http_response(r, "LLM")
            data = r.json()
        content = (data["choices"][0]["message"]["content"] or "").strip()
        prompt_tokens, completion_tokens, total_tokens = token_fields(data.get("usage"))
        _log_usage(data, use_model=use_model, prefix="大模型同步完成")
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "大模型同步完成 模型=%s 状态码=%s 耗时毫秒=%d 响应字符数=%d 预览=%s",
            use_model,
            status_code,
            elapsed_ms,
            len(content),
            _truncate(content, 200),
        )
        return content
    except Exception as ex:
        error = ex
        raise
    finally:
        if error is not None:
            record_ai_call(
                kind="chat",
                scene=scene,
                model=use_model,
                status="error",
                http_status=status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                error_message=format_error(error),
                request_text=format_messages(messages),
                response_text=content or None,
            )
        else:
            record_ai_call(
                kind="chat",
                scene=scene,
                model=use_model,
                status="success",
                http_status=status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                error_message=None,
                request_text=format_messages(messages),
                response_text=content,
            )


def _resolve_llm_cfg(
    *,
    db: Session | None,
    cfg: LlmRuntime | None,
) -> LlmRuntime:
    if cfg is not None:
        return cfg
    if db is None:
        raise AppError("bad_request", LLM_RUNTIME_NOT_CONFIGURED, status_code=400)
    return get_llm_runtime(db)


def chat_complete_stream(
    messages: list[dict[str, Any]],
    *,
    db: Session | None = None,
    cfg: LlmRuntime | None = None,
    temperature: float = 0.2,
    model: str | None = None,
    scene: str | None = None,
) -> Iterator[str]:
    runtime = _resolve_llm_cfg(db=db, cfg=cfg)
    use_model = model if model is not None else runtime.model
    if not runtime.api_key.strip():
        record_ai_call(
            kind="chat_stream",
            scene=scene,
            model=use_model,
            status="error",
            http_status=None,
            latency_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            error_message="未配置 LLM API Key，请在系统 AI 配置或 .env 中设置",
            request_text=format_messages(messages),
            response_text=None,
        )
        raise AppError(
            "bad_request",
            "未配置 LLM API Key，请在系统 AI 配置或 .env 中设置",
            status_code=400,
        )
    url = f"{runtime.api_base.rstrip('/')}/chat/completions"
    body: dict[str, Any] = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "response_format": {"type": "json_object"},
    }
    log.info(
        "大模型流式请求 模型=%s 地址=%s 温度=%s 流式=true 消息数=%d 预览=%s",
        use_model,
        url,
        temperature,
        len(messages),
        _preview_messages(messages),
    )
    started = time.perf_counter()
    status_code: int | None = None
    parts: list[str] = []
    last_usage: dict[str, Any] | None = None
    error: BaseException | None = None
    try:
        with httpx.Client(timeout=120.0) as client:
            with client.stream(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {runtime.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as r:
                status_code = r.status_code
                check_ai_http_response(r, "LLM")
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        last_usage = usage
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        parts.append(content)
                        yield content
    except Exception as ex:
        error = ex
        raise
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        total_chars = sum(len(part) for part in parts)
        if error is None and total_chars == 0:
            log.warning(
                "大模型流式响应为空 模型=%s 状态码=%s 耗时毫秒=%d 地址=%s",
                use_model,
                status_code,
                elapsed_ms,
                url,
            )
        elif error is None:
            log.info(
                "大模型流式完成 模型=%s 状态码=%s 耗时毫秒=%d 响应字符数=%d",
                use_model,
                status_code,
                elapsed_ms,
                total_chars,
            )
        prompt_tokens, completion_tokens, total_tokens = token_fields(last_usage)
        record_ai_call(
            kind="chat_stream",
            scene=scene,
            model=use_model,
            status="error" if error is not None else "success",
            http_status=status_code,
            latency_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            error_message=format_error(error) if error is not None else None,
            request_text=format_messages(messages),
            response_text="".join(parts) or None,
        )
