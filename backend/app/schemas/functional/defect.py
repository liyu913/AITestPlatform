from datetime import datetime

from pydantic import BaseModel, Field


class DefectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    severity: str = Field(default="P2", max_length=64)
    status: str = Field(default="open", max_length=64)
    case_id: int | None = None
    cycle_item_id: int | None = None
    requirement_id: int | None = None
    assignee_id: int | None = None


class DefectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    severity: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    case_id: int | None = None
    cycle_item_id: int | None = None
    requirement_id: int | None = None
    assignee_id: int | None = None


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
    case_id: int | None
    case_title: str | None = None
    cycle_item_id: int | None
    requirement_id: int | None
    reporter_id: int | None
    assignee_id: int | None
    external_system: str | None
    external_id: str | None
    created_at: datetime
    updated_at: datetime
    attachments: list[DefectAttachmentOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}
