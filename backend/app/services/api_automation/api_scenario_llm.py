"""Thin LLM helper for LangGraph nodes — reuses get_llm_runtime / chat_complete."""

from __future__ import annotations

import json
from typing import Any

from app.services.ai import llm
from app.services.ai.effective_ai_settings import LlmRuntime


def llm_json(
    messages: list[dict[str, Any]],
    *,
    cfg: LlmRuntime,
    temperature: float = 0.2,
    scene: str | None = None,
) -> dict[str, Any]:
    raw = llm.chat_complete(messages, cfg=cfg, temperature=temperature, scene=scene)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM 返回非 JSON 对象")
    return data
