from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.functional.design_trace import CaseDesignTrace

CASE_TYPES = ("功能", "边界", "异常", "权限")

LifecycleStatus = Literal["draft", "pending_review", "confirmed", "archived"]
ExecutionResult = Literal["untested", "passed", "failed", "blocked", "error"]


def clean_precondition(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def clean_case_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned in CASE_TYPES:
        return cleaned
    return None


class TestCaseStepIn(BaseModel):
    order: int = 0
    action: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class TestCaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    module: str | None = Field(default=None, max_length=128)
    precondition: str | None = Field(default=None, max_length=65535)
    test_data: str | None = Field(default=None, max_length=65535)
    variables: dict[str, str] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, max_length=32)
    overall_expected: str = Field(default="", max_length=65535)
    requirement_id: int | None = None
    priority: str | None = Field(default=None, max_length=64)
    lifecycle_status: str | None = Field(default=None, max_length=32)
    # Deprecated alias; accepted only when it is a lifecycle word.
    status: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = None
    steps: list[TestCaseStepIn] = Field(default_factory=list)

    @field_validator("precondition", "test_data", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return clean_precondition(value)

    @field_validator("variables", mode="before")
    @classmethod
    def _variables(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                continue
            out[key] = "" if v is None else str(v)
        return out

    @field_validator("case_type", mode="before")
    @classmethod
    def _case_type(cls, value: object) -> str | None:
        return clean_case_type(value)


class TestCaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    module: str | None = Field(default=None, max_length=128)
    precondition: str | None = Field(default=None, max_length=65535)
    test_data: str | None = Field(default=None, max_length=65535)
    variables: dict[str, str] | None = None
    case_type: str | None = Field(default=None, max_length=32)
    overall_expected: str | None = Field(default=None, max_length=65535)
    requirement_id: int | None = None
    priority: str | None = Field(default=None, max_length=64)
    lifecycle_status: LifecycleStatus | None = None
    # Deprecated alias; execution words are rejected by the API layer.
    status: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = None
    steps: list[TestCaseStepIn] | None = None
    change_note: str | None = Field(default=None, max_length=512)

    @field_validator("precondition", "test_data", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return clean_precondition(value)

    @field_validator("variables", mode="before")
    @classmethod
    def _variables(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                continue
            out[key] = "" if v is None else str(v)
        return out

    @field_validator("case_type", mode="before")
    @classmethod
    def _case_type(cls, value: object) -> str | None:
        return clean_case_type(value)


class TestCaseStepOut(BaseModel):
    id: int
    order: int
    action: str
    expected: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestCaseOut(BaseModel):
    ai_draft_id: str | None = None
    design_trace: CaseDesignTrace | None = None
    id: int
    project_id: int
    requirement_id: int | None
    title: str
    module: str | None = None
    precondition: str | None = None
    test_data: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    case_type: str | None = None
    overall_expected: str
    priority: str | None
    # Legacy mirror of lifecycle_status, kept so old clients keep rendering.
    status: str
    lifecycle_status: str = "draft"
    last_result: str | None = None
    last_result_at: datetime | None = None
    version_no: int = 1
    published_version_id: int | None = None
    published_version_no: int | None = None
    published_at: datetime | None = None
    assigned_reviewer_id: int | None = None
    review_due_at: datetime | None = None
    archived_at: datetime | None = None
    tags_json: list[str] | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime
    steps: list[TestCaseStepOut]
    linked_ui_case_count: int = 0

    model_config = {"from_attributes": True}


class TestCaseVersionOut(BaseModel):
    id: int
    case_id: int
    version_no: int
    lifecycle_status: str
    change_source: str
    change_note: str | None = None
    created_by_id: int | None = None
    created_at: datetime
    snapshot: dict | None = None

    model_config = {"from_attributes": True}


class TestCaseReviewIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=65535)
    version_id: int | None = None


class TestCaseReviewOut(BaseModel):
    id: int
    case_id: int
    version_id: int | None = None
    version_no: int
    decision: str
    comment: str = ""
    reviewer_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TestCaseArchiveIn(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


class TestCaseSubmitReviewIn(BaseModel):
    reviewer_id: int | None = None
    due_at: datetime | None = None


class TestCasePublishIn(BaseModel):
    version_no: int | None = Field(default=None, ge=1)


class ReviewQueueItemOut(BaseModel):
    case_id: int
    title: str
    version_no: int
    submitted_at: datetime | None = None
    submitted_by_id: int | None = None
    review_count: int = 0
    assigned_reviewer_id: int | None = None
    assigned_reviewer_name: str | None = None
    due_at: datetime | None = None
