from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class RequirementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    body_md: str = Field(default="")
    status: str = Field(default="draft", max_length=64)
    priority: str | None = Field(default=None, max_length=64)
    acceptance_rules: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def require_body(self) -> "RequirementCreate":
        if not (self.body_md or "").strip():
            raise ValueError("body_md is required when creating without attachments")
        return self


class RequirementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    body_md: str | None = None
    status: str | None = Field(default=None, max_length=64)
    priority: str | None = Field(default=None, max_length=64)
    acceptance_rules: str | None = Field(default=None, max_length=20000)
    change_note: str | None = Field(default=None, max_length=512)


class RequirementVersionOut(BaseModel):
    id: int
    requirement_id: int
    version_no: int
    change_source: str
    change_note: str | None = None
    created_by_id: int | None = None
    created_at: datetime
    snapshot: dict | None = None

    model_config = {"from_attributes": True}


class RequirementAnalysisFeatureItem(BaseModel):
    name: str
    detail: str = ""


class RequirementAnalysisFeatureModule(BaseModel):
    module: str
    items: list[RequirementAnalysisFeatureItem] = Field(default_factory=list)


class RequirementAnalysisUiPage(BaseModel):
    name: str
    description: str = ""
    areas: list[str] = Field(default_factory=list)


class RequirementAnalysisUi(BaseModel):
    summary: str = ""
    pages: list[RequirementAnalysisUiPage] = Field(default_factory=list)


class RequirementAnalysisUnclear(BaseModel):
    item: str
    reason: str = ""


def _normalize_features(value: Any) -> list[dict[str, Any]]:
    """Accept module groups or legacy flat [{name, detail}] lists."""
    if not isinstance(value, list):
        return []
    if not value:
        return []
    first = value[0]
    if isinstance(first, dict) and "module" in first:
        return value
    if isinstance(first, dict) and ("name" in first or "detail" in first):
        items = [
            {"name": str(row.get("name") or "未命名"), "detail": str(row.get("detail") or "")}
            for row in value
            if isinstance(row, dict)
        ]
        return [{"module": "未分组", "items": items}] if items else []
    return value


class RequirementAnalysis(BaseModel):
    features: list[RequirementAnalysisFeatureModule] = Field(default_factory=list)
    ui: RequirementAnalysisUi = Field(default_factory=RequirementAnalysisUi)
    unclear: list[RequirementAnalysisUnclear] = Field(default_factory=list)
    image_count: int = 0

    @field_validator("features", mode="before")
    @classmethod
    def coerce_features(cls, value: Any) -> Any:
        return _normalize_features(value)


class AnalysisChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AnalysisChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    analysis: RequirementAnalysis | None = None


class AnalysisSaveIn(BaseModel):
    analysis: RequirementAnalysis


class AnalysisChatOut(BaseModel):
    requirement_id: int
    analysis: RequirementAnalysis
    pending_analysis: RequirementAnalysis | None = None
    analyzed_at: datetime | None = None
    messages: list[AnalysisChatMessage] = Field(default_factory=list)
    reply: str = ""


class RequirementImportBodyOut(BaseModel):
    body_md: str


class RequirementAttachmentOut(BaseModel):
    id: int
    requirement_id: int
    filename: str
    mime: str
    size_bytes: int
    uploader_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementOut(BaseModel):
    id: int
    project_id: int
    title: str
    body_md: str
    status: str
    priority: str | None
    acceptance_rules: str = ""
    version_no: int = 1
    creator_id: int | None
    attachments: list[RequirementAttachmentOut] = Field(default_factory=list)
    analysis_json: RequirementAnalysis | None = None
    analyzed_at: datetime | None = None
    analysis_messages_json: list[dict] | None = None
    analysis_pending_json: RequirementAnalysis | None = None
    plan_draft_md: str | None = None
    plan_draft_at: datetime | None = None
    plan_draft_messages_json: list[dict] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments)
