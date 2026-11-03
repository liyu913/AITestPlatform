from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TestPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    version: str | None = Field(default=None, max_length=128)
    iteration: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=65535)
    status: str = Field(default="draft", max_length=64)


class TestPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    version: str | None = Field(default=None, max_length=128)
    iteration: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=65535)
    status: str | None = Field(default=None, max_length=64)
    body_md: str | None = None


class TestPlanOut(BaseModel):
    id: int
    project_id: int
    name: str
    version: str | None
    iteration: str | None
    description: str | None
    status: str
    requirement_id: int | None = None
    requirement_title: str | None = None
    body_md: str | None = None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LinkedTestPlanRef(BaseModel):
    id: int
    name: str


class PlanDraftChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class TestPlanDraftOut(BaseModel):
    requirement_id: int
    body_md: str
    drafted_at: datetime
    linked_plans: list[LinkedTestPlanRef] = Field(default_factory=list)
    messages: list[PlanDraftChatMessage] = Field(default_factory=list)


class TestPlanDraftChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    body_md: str | None = None


class TestPlanDraftAccept(BaseModel):
    mode: Literal["create", "update"]
    plan_id: int | None = None
    body_md: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=512)
    version: str | None = Field(default=None, max_length=128)
    iteration: str | None = Field(default=None, max_length=128)


class TestCycleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    status: str = Field(default="draft", max_length=64)


class TestCycleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    status: str | None = Field(default=None, max_length=64)


class TestCycleOut(BaseModel):
    id: int
    plan_id: int
    name: str
    status: str
    creator_id: int | None
    created_at: datetime
    updated_at: datetime
    item_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    blocked_count: int = 0
    untested_count: int = 0

    model_config = {"from_attributes": True}


class CycleItemsAdd(BaseModel):
    case_ids: list[int] = Field(min_length=1)


class CycleItemsAssign(BaseModel):
    item_ids: list[int] = Field(min_length=1)
    assignee_id: int | None = None


class CycleItemUpdate(BaseModel):
    result: str | None = Field(default=None, max_length=64)
    actual_result: str | None = Field(default=None, max_length=65535)
    assignee_id: int | None = None


class ExecutionAttachmentOut(BaseModel):
    id: int
    cycle_item_id: int
    filename: str
    mime: str
    size_bytes: int
    uploader_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CycleItemOut(BaseModel):
    id: int
    cycle_id: int
    case_id: int
    case_title: str | None = None
    case_priority: str | None = None
    requirement_id: int | None = None
    assignee_id: int | None
    result: str
    actual_result: str
    executed_by_id: int | None
    executed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: list[ExecutionAttachmentOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RequirementCoverageOut(BaseModel):
    requirement_id: int
    title: str
    status: str
    priority: str | None
    case_total: int
    passed: int
    failed: int
    blocked: int
    untested: int
    coverage_ratio: float
    blind: bool
