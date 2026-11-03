"""Schemas for LangGraph API scenario agent sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ScenarioAgentSessionCreate(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    definition_ids: list[int] = Field(default_factory=list, min_length=1)
    # backward compatible alias
    case_ids: list[int] | None = None

    @model_validator(mode="after")
    def _require_defs(self) -> ScenarioAgentSessionCreate:
        if not self.definition_ids and self.case_ids:
            # old clients mistakenly sending case_ids — reject with clear message via empty defs
            pass
        if not self.definition_ids:
            raise ValueError("请至少选择一个接口定义 definition_ids")
        return self


class ScenarioAgentMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ScenarioAgentConfirm(BaseModel):
    action: Literal["confirm", "cancel"]


class ScenarioAgentSessionSummary(BaseModel):
    id: int
    title: str
    status: str
    updated_at: datetime
    message_count: int


class ScenarioAgentSessionOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    thread_id: str
    status: str
    prompt: str
    candidate_definition_ids: list[int] = Field(default_factory=list)
    candidate_case_ids: list[int] = Field(default_factory=list)
    messages: list[dict[str, str]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    suite_id: int | None = None
    run_id: int | None = None
    error_message: str = ""
    created_at: datetime
    updated_at: datetime


class ScenarioTriageConfirm(BaseModel):
    action: Literal["confirm", "cancel"]
    apply_patch: bool = False


class ScenarioTriageOut(BaseModel):
    id: str
    run_id: int
    step_id: int
    status: str
    plan: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str = ""
    patch_message: str = ""
