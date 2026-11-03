from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TestCaseRunMode = Literal["agent_once", "manual"]
TestCaseRunStatus = Literal["pending", "running", "passed", "failed", "error", "cancelled"]


class TestCaseRunOut(BaseModel):
    id: int
    project_id: int
    case_id: int
    case_title: str = ""
    triggered_by: int | None = None
    mode: str
    status: str
    env_id: int | None = None
    session_id: int | None = None
    summary: str = ""
    error_message: str = ""
    actual_result: str = ""
    summary_json: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestCaseRunListFilters(BaseModel):
    case_id: int | None = None
    status: str | None = Field(default=None, max_length=32)
    mode: str | None = Field(default=None, max_length=32)
