"""Schemas for UI AI explore jobs."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.ui_automation.ui_automation import BrowserName, UiStepIn


class UiAiExploreCreate(BaseModel):
    testcase_id: int
    env_id: int | None = None
    account_name: str | None = Field(default=None, max_length=128)
    browser: BrowserName = "chromium"
    max_steps: int = Field(default=25, ge=1, le=50)


class UiAiExploreConfirmSave(BaseModel):
    name: str | None = Field(default=None, max_length=512)
    script: str | None = Field(default=None, max_length=200000)
    steps: list[UiStepIn] | None = None
    status: Literal["draft", "ready"] = "ready"


class UiAiExploreJobOut(BaseModel):
    id: int
    project_id: int
    testcase_id: int
    env_id: int | None
    account_name: str | None
    browser: str
    status: str
    plan_json: dict[str, Any] | list[Any] | None = None
    trace_json: list[Any] | None = None
    result_steps: list[dict[str, Any]] = Field(default_factory=list)
    playwright_code: str = ""
    dry_run_status: str | None = None
    dry_run_error: str = ""
    error_message: str = ""
    saved_ui_case_id: int | None = None
    creator_id: int | None = None
    created_at: datetime
    updated_at: datetime
