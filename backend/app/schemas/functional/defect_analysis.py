from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AnalysisCategory = Literal["product", "environment", "data", "script", "pending"]
AnalysisStatus = Literal["draft", "confirmed", "dismissed", "converted"]
SubmissionTarget = Literal["internal", "external"]


class AnalysisCreate(BaseModel):
    cycle_item_id: int | None = None
    defect_id: int | None = None
    title: str | None = Field(default=None, max_length=512)
    observed: str | None = Field(default=None, max_length=65535)
    expected: str | None = Field(default=None, max_length=65535)
    category: AnalysisCategory = "pending"
    conclusion: str | None = Field(default=None, max_length=65535)


class AnalysisUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    observed: str | None = Field(default=None, max_length=65535)
    expected: str | None = Field(default=None, max_length=65535)
    category: AnalysisCategory | None = None
    conclusion: str | None = Field(default=None, max_length=65535)
    status: Literal["confirmed", "dismissed"] | None = None


class AnalysisOut(BaseModel):
    id: int
    project_id: int
    defect_id: int | None = None
    source_type: str
    source_id: int | None = None
    title: str
    observed: str
    expected: str
    category: str
    conclusion: str
    status: str
    evidence_json: dict | None = None
    confirmed_by_id: int | None = None
    confirmed_at: datetime | None = None
    created_by_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubmissionIn(BaseModel):
    target: SubmissionTarget = "internal"
    request_id: str | None = Field(default=None, max_length=64)
    connector: str | None = Field(default=None, max_length=64)


class SubmissionOut(BaseModel):
    id: int
    project_id: int
    defect_id: int
    target: str
    connector: str | None = None
    request_id: str
    status: str
    external_id: str | None = None
    external_url: str | None = None
    message: str
    attempt_no: int
    submitted_by_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
