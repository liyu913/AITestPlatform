"""Schemas for LangGraph API single-case agent sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CaseAgentSessionCreate(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    definition_ids: list[int] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _require_defs(self) -> CaseAgentSessionCreate:
        if not self.definition_ids:
            raise ValueError("请至少选择一个接口定义 definition_ids")
        return self


class CaseAgentMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class CaseAgentConfirm(BaseModel):
    action: Literal["confirm", "cancel"]


class CaseAgentSessionSummary(BaseModel):
    id: int
    title: str
    status: str
    updated_at: datetime
    message_count: int


class CaseAgentSessionOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    thread_id: str
    status: str
    prompt: str
    candidate_definition_ids: list[int] = Field(default_factory=list)
    messages: list[dict[str, str]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    created_case_ids: list[int] = Field(default_factory=list)
    run_id: int | None = None
    error_message: str = ""
    created_at: datetime
    updated_at: datetime
