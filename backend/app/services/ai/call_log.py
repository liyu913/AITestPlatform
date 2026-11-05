"""Persist one row per LLM or embedding HTTP call. Failures here never break the call."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.ai.ai_call_log import AiCallLog
from app.services.ai.call_context import (
    action_var,
    binding_id_var,
    config_source_var,
    project_id_var,
    request_id_var,
    resolved_config_var,
    skill_version_id_var,
    user_id_var,
)

log = logging.getLogger(__name__)

TEXT_LIMIT = 8000

_session_factory: Callable[[], Session] | None = None


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    global _session_factory
    _session_factory = factory


def clip_text(text: str | None, limit: int = TEXT_LIMIT) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def format_error(exc: BaseException) -> str:
    if isinstance(exc, AppError) and exc.message.strip():
        return exc.message.strip()
    text = str(exc).strip()
    return text or type(exc).__name__


def token_fields(usage: Any) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None, None

    def one(key: str) -> int | None:
        val = usage.get(key)
        if isinstance(val, bool) or not isinstance(val, int):
            return None
        return val

    return one("prompt_tokens"), one("completion_tokens"), one("total_tokens")


def render_message_content(content: Any) -> str:
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, dict) and (part.get("type") == "image_url" or "image_url" in part):
                bits.append("[图片]")
                continue
            if isinstance(part, dict) and part.get("type") == "text":
                bits.append(str(part.get("text") or ""))
                continue
            bits.append(str(part))
        return "\n".join(bits)
    return str(content or "")


def format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "?")
        lines.append(f"[{role}]\n{render_message_content(message.get('content'))}")
    return "\n\n".join(lines)


def _open_session() -> Session:
    if _session_factory is not None:
        return _session_factory()
    from app.deps import SessionLocal

    return SessionLocal()


def record_ai_call(
    *,
    kind: str,
    scene: str | None,
    model: str | None,
    status: str,
    http_status: int | None,
    latency_ms: int | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    error_message: str | None,
    request_text: str | None,
    response_text: str | None,
) -> None:
    try:
        db = _open_session()
        try:
            db.add(
                AiCallLog(
                    kind=kind[:32],
                    scene=clip_text(scene, 64),
                    model=clip_text(model, 256),
                    status=status[:16],
                    http_status=http_status,
                    latency_ms=latency_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    error_message=clip_text(error_message),
                    request_text=clip_text(request_text),
                    response_text=clip_text(response_text),
                    user_id=user_id_var.get(),
                    project_id=project_id_var.get(),
                    request_id=clip_text(request_id_var.get(), 64),
                    action=clip_text(action_var.get(), 32),
                    skill_version_id=skill_version_id_var.get(),
                    binding_id=binding_id_var.get(),
                    config_source=clip_text(config_source_var.get(), 16),
                    resolved_config=resolved_config_var.get(),
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("写入 AI 调用日志失败")
