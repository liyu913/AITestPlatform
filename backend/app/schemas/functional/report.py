from datetime import datetime

from pydantic import BaseModel, Field


class ReportSummaryOut(BaseModel):
    id: int
    project_id: int
    cycle_id: int
    revision: int
    generated_at: datetime
    generated_by_id: int | None = None
    summary_text: str = ""
    cycle_name: str | None = None
    plan_name: str | None = None
    target_build: str | None = None
    pass_rate: float | None = None
    completion_rate: float | None = None
    risk_count: int = 0


class ReportOut(ReportSummaryOut):
    snapshot: dict | None = Field(default=None)


class ReportExportOut(BaseModel):
    filename: str
    media_type: str


class ReportMetricDelta(BaseModel):
    key: str
    label: str
    current: float | None = None
    previous: float | None = None
    delta: float | None = None


class ReportRequirementChange(BaseModel):
    requirement_id: int
    title: str
    previous_ratio: float | None = None
    current_ratio: float | None = None
    previous_case_total: int = 0
    current_case_total: int = 0


class ReportCompareOut(BaseModel):
    cycle_id: int
    current_id: int
    previous_id: int
    current_revision: int
    previous_revision: int
    metrics: list[ReportMetricDelta] = Field(default_factory=list)
    risks_added: list[str] = Field(default_factory=list)
    risks_resolved: list[str] = Field(default_factory=list)
    requirements_changed: list[ReportRequirementChange] = Field(default_factory=list)
