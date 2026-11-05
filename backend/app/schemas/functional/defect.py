from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DefectCategory = Literal["product", "environment", "data", "script", "rule_pending", "ux"]
DefectStatus = Literal["open", "in_progress", "resolved", "closed", "reopened"]
FailureSourceType = Literal["cycle_item", "api_run", "ui_run", "test_case_run", "manual"]


class DefectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    severity: str = Field(default="P2", max_length=64)
    status: DefectStatus = "open"
    category: DefectCategory = "product"
    case_id: int | None = None
    cycle_item_id: int | None = None
    requirement_id: int | None = None
    assignee_id: int | None = None
    failure_source_type: FailureSourceType | None = None
    failure_source_id: int | None = None
    expected_result: str = Field(default="", max_length=65535)
    actual_result: str = Field(default="", max_length=65535)
    environment: str | None = Field(default=None, max_length=256)
    found_build: str | None = Field(default=None, max_length=128)


class DefectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    severity: str | None = Field(default=None, max_length=64)
    status: DefectStatus | None = None
    category: DefectCategory | None = None
    case_id: int | None = None
    cycle_item_id: int | None = None
    requirement_id: int | None = None
    assignee_id: int | None = None
    expected_result: str | None = Field(default=None, max_length=65535)
    actual_result: str | None = Field(default=None, max_length=65535)
    environment: str | None = Field(default=None, max_length=256)
    found_build: str | None = Field(default=None, max_length=128)
    fix_build: str | None = Field(default=None, max_length=128)
    # Shown in the status history for a status change made through PATCH.
    status_comment: str | None = Field(default=None, max_length=2000)


class DefectAttachmentOut(BaseModel):
    id: int
    defect_id: int
    filename: str
    mime: str
    size_bytes: int
    uploader_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DefectOut(BaseModel):
    id: int
    project_id: int
    title: str
    description: str
    severity: str
    status: str
    category: str = "product"
    case_id: int | None
    case_title: str | None = None
    cycle_item_id: int | None
    requirement_id: int | None
    failure_source_type: str | None = None
    failure_source_id: int | None = None
    expected_result: str = ""
    actual_result: str = ""
    environment: str | None = None
    found_build: str | None = None
    fix_build: str | None = None
    verified_at: datetime | None = None
    verified_by_id: int | None = None
    verification_result: str | None = None
    verification_note: str = ""
    regression_cycle_ids: list[int] = Field(default_factory=list)
    closed_at: datetime | None = None
    closed_by_id: int | None = None
    reporter_id: int | None
    assignee_id: int | None
    external_system: str | None
    external_id: str | None
    created_at: datetime
    updated_at: datetime
    attachments: list[DefectAttachmentOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class DefectVerifyIn(BaseModel):
    """Retest of the original problem against the build that is supposed to fix it."""

    result: Literal["passed", "failed"]
    fix_build: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=65535)
    cycle_id: int | None = None


class DefectRegressionIn(BaseModel):
    cycle_ids: list[int] = Field(default_factory=list)


class DefectHistoryOut(BaseModel):
    id: int
    defect_id: int
    from_status: str
    to_status: str
    comment: str = ""
    actor_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DefectSuggestionOut(BaseModel):
    """Rule-based draft the tester can edit before creating the defect."""

    category: str
    severity: str
    title: str
    description: str
    expected_result: str
    actual_result: str
    environment: str | None = None
    found_build: str | None = None
    reasons: list[str] = Field(default_factory=list)


class DefectEvidenceCycle(BaseModel):
    id: int | None = None
    name: str | None = None
    plan_name: str | None = None
    target_build: str | None = None
    environment: str | None = None


class DefectEvidenceItem(BaseModel):
    id: int | None = None
    asset_type: str | None = None
    asset_title: str | None = None
    result: str | None = None
    actual_result: str = ""
    executed_at: datetime | None = None
    attempt_no: int = 0


class DefectEvidenceAttempt(BaseModel):
    id: int
    attempt_no: int
    execution_type: str
    status: str
    summary: str = ""
    original_run_type: str | None = None
    original_run_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DefectEvidenceOut(BaseModel):
    """Before/after evidence panel: original failure plus retest history."""

    defect_id: int
    cycle: DefectEvidenceCycle | None = None
    item: DefectEvidenceItem | None = None
    attempts: list[DefectEvidenceAttempt] = Field(default_factory=list)
    verification_result: str | None = None
    verification_note: str = ""
    verified_at: datetime | None = None
    history: list[DefectHistoryOut] = Field(default_factory=list)


class RegressionTaskIn(BaseModel):
    defect_ids: list[int] = Field(min_length=1)
    cycle_name: str = Field(default="回归验证", min_length=1, max_length=512)
    plan_id: int | None = None
    target_build: str | None = Field(default=None, max_length=128)
    env_kind: str | None = Field(default=None, max_length=16)
    env_id: int | None = None


class RegressionTaskOut(BaseModel):
    cycle_id: int
    plan_id: int
    item_count: int
    defect_ids: list[int]
