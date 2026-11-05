import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.functional.testcase import clean_case_type, clean_precondition
from app.schemas.functional.design_trace import CaseDesignTrace
from app.services.ai.case_review_dims import (
    DEFAULT_WEIGHT,
    MAX_WEIGHT,
    MIN_WEIGHT,
    REVIEW_DIM_KEYS,
)


class PromptMessage(BaseModel):
    """Internal prompt, including server-side requirement document content."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatMessage(PromptMessage):
    """User-supplied chat message; document enrichment must not use this limit."""

    content: str = Field(min_length=1, max_length=16000)


class ReviewDimensionConfig(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    weight: int = Field(default=DEFAULT_WEIGHT, ge=MIN_WEIGHT, le=MAX_WEIGHT)

    @field_validator("key")
    @classmethod
    def _known_key(cls, value: str) -> str:
        key = (value or "").strip()
        if key not in REVIEW_DIM_KEYS:
            raise ValueError(f"unknown review dimension: {value}")
        return key


class GeneratedCaseStep(BaseModel):
    order: int = 0
    action: str = Field(min_length=1)
    expected: str | None = None


class GeneratedCase(BaseModel):
    draft_id: str | None = None
    design_trace: CaseDesignTrace | None = None
    title: str = Field(min_length=1, max_length=512)
    overall_expected: str | None = Field(default=None, max_length=65535)
    module: str | None = Field(default=None, max_length=128)
    precondition: str | None = Field(default=None, max_length=65535)
    test_data: str | None = Field(default=None, max_length=65535)
    case_type: str | None = Field(default=None, max_length=32)
    priority: str | None = Field(default=None, max_length=64)
    steps: list[GeneratedCaseStep] = Field(default_factory=list)

    @field_validator("draft_id")
    @classmethod
    def _valid_draft_id(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None

    @field_validator("precondition", mode="before")
    @classmethod
    def _precondition(cls, value: object) -> str | None:
        return clean_precondition(value)

    @field_validator("test_data", mode="before")
    @classmethod
    def _test_data(cls, value: object) -> str | None:
        return clean_precondition(value)

    @field_validator("case_type", mode="before")
    @classmethod
    def _case_type(cls, value: object) -> str | None:
        return clean_case_type(value)


class AgentArtifacts(BaseModel):
    requirement_analysis: dict | None = None
    test_points: list[dict] = Field(default_factory=list)
    review: dict | None = None


class AiGenerateRequest(BaseModel):
    requirement_id: int | None = None
    user_notes: str | None = None
    messages: list[ChatMessage] | None = None
    llm_model: str | None = Field(default=None, max_length=128)
    knowledge_library_id: int | None = None
    generate_mode: Literal["fast", "agent"] = "fast"
    current_drafts: list[GeneratedCase] = Field(default_factory=list)
    agent_resume_from: Literal["cases"] | None = None
    agent_artifacts: AgentArtifacts | None = None
    enable_case_review: bool = False
    review_dimensions: list[ReviewDimensionConfig] = Field(default_factory=list)


class LlmOptionsOut(BaseModel):
    case_design_skill: str | None = None
    default_model: str
    models: list[str]


class AiGenerateResponse(BaseModel):
    cases: list[GeneratedCase]
    assistant_message: str = ""


class AiConfirmRequest(BaseModel):
    cases: list[GeneratedCase] = Field(min_length=1)
    requirement_id: int | None = None


class AiSessionUpsert(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    messages: list[ChatMessage] = Field(default_factory=list)
    drafts: list[GeneratedCase] = Field(default_factory=list)
    requirement_id: int | None = None
    knowledge_library_id: int | None = None
    llm_model: str | None = Field(default=None, max_length=128)
    generate_mode: Literal["fast", "agent"] = "fast"
    artifacts: AgentArtifacts | None = None


class AiSessionOut(BaseModel):
    id: int
    project_id: int
    title: str
    messages: list[ChatMessage]
    drafts: list[GeneratedCase]
    requirement_id: int | None = None
    knowledge_library_id: int | None = None
    llm_model: str | None = None
    generate_mode: Literal["fast", "agent"] = "fast"
    artifacts: AgentArtifacts | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AiSessionSummary(BaseModel):
    id: int
    title: str
    updated_at: datetime.datetime
    message_count: int
    draft_count: int
