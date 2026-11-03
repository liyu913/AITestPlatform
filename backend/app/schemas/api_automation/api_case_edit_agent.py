"""Schemas for LangGraph API case batch-edit agent sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


_MAX_CASES = 20


class CaseEditAgentSessionCreate(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    case_ids: list[int] = Field(min_length=1, max_length=_MAX_CASES)

    @field_validator("case_ids")
    @classmethod
    def _unique_ids(cls, v: list[int]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for x in v:
            i = int(x)
            if i in seen:
                continue
            seen.add(i)
            out.append(i)
        if not out:
            raise ValueError("请至少选择一条用例")
        if len(out) > _MAX_CASES:
            raise ValueError(f"一次最多修订 {_MAX_CASES} 条用例")
        return out

    @model_validator(mode="after")
    def _require_cases(self) -> CaseEditAgentSessionCreate:
        if not self.case_ids:
            raise ValueError("请至少选择一条用例 case_ids")
        return self


class CaseEditAgentMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class CaseEditAgentConfirm(BaseModel):
    action: Literal["confirm", "cancel"]
    case_ids: list[int] | None = None


class CaseEditAgentSessionSummary(BaseModel):
    id: int
    title: str
    status: str
    updated_at: datetime
    message_count: int
    case_count: int = 0


class CaseEditAgentSessionOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    thread_id: str
    status: str
    prompt: str
    case_ids: list[int] = Field(default_factory=list)
    base_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, str]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    applied_case_ids: list[int] = Field(default_factory=list)
    error_message: str = ""
    created_at: datetime
    updated_at: datetime
