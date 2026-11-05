"""Schemas for conversational UI scenario agent sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class UiScenarioAgentSessionCreate(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    testcase_id: int | None = None
    env_id: int | None = None
    browser: str | None = Field(default=None, max_length=32)
    headed: bool = True
    max_steps: int = Field(default=25, ge=1, le=50)
    engine: Literal["midscene"] = "midscene"
    # None = follow env inject; none = skip inject (login cases)
    auth_mode: Literal["none", "account", "token"] | None = None
    account_name: str | None = Field(default=None, max_length=128)
    execution_mode: Literal["explore_codegen", "agent_once"] = "explore_codegen"
    # Optional test batch item this run belongs to (B4).
    cycle_item_id: int | None = None


class UiScenarioAgentMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class UiScenarioAgentConfirm(BaseModel):
    action: Literal["confirm", "cancel"]
    name: str | None = Field(default=None, max_length=512)
    script: str | None = Field(default=None, max_length=200000)
    status: Literal["draft", "ready"] = "ready"


class UiScenarioAgentTrialRun(BaseModel):
    case_id: str = Field(default="", max_length=64)
    headed: bool = True
    script: str | None = Field(default=None, max_length=200000)


class UiScenarioAgentSessionSummary(BaseModel):
    id: int
    title: str
    status: str
    updated_at: datetime
    message_count: int


class UiScenarioAgentSessionOut(BaseModel):
    id: int
    project_id: int
    user_id: int
    thread_id: str
    status: str
    prompt: str
    testcase_id: int | None = None
    env_id: int | None = None
    browser: str = "chromium"
    headed: bool = True
    messages: list[dict[str, str]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    matrix: list[dict[str, Any]] = Field(default_factory=list)
    explore_report: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)
    result_steps: list[dict[str, Any]] = Field(default_factory=list)
    playwright_code: str = ""
    has_credentials: bool = False
    events: list[dict[str, Any]] = Field(default_factory=list)
    dry_run_status: str | None = None
    dry_run_error: str = ""
    error_message: str = ""
    saved_ui_case_id: int | None = None
    run_id: int | None = None
    case_run_id: int | None = None
    execution_mode: str = "explore_codegen"
    auth_mode: str | None = None
    account_name: str | None = None
    created_at: datetime
    updated_at: datetime
