from pydantic import BaseModel, Field, field_validator

from app.schemas.ai.ai_gen import GeneratedCase
from app.services.ai.case_review_dims import REVIEW_DIM_KEYS


class RequirementAnalysisOut(BaseModel):
    is_clear: bool
    summary: str = ""
    ambiguities: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class TestPointOut(BaseModel):
    id: str
    title: str
    category: str = ""
    priority_hint: str | None = None


class TestPointsResponse(BaseModel):
    test_points: list[TestPointOut] = Field(default_factory=list)


class CaseReviewIssue(BaseModel):
    case_title: str = ""
    issue_type: str = ""
    detail: str = ""

    @field_validator("issue_type", mode="before")
    @classmethod
    def _normalize_issue_type(cls, value: object) -> str:
        key = str(value or "").strip()
        if key in REVIEW_DIM_KEYS:
            return key
        # Keep free-form for older mocks; UI maps unknown to raw label.
        return key


class CaseReviewResult(BaseModel):
    summary: str = ""
    issues: list[CaseReviewIssue] = Field(default_factory=list)
    dimension_scores: dict[str, int] | None = None

    @field_validator("dimension_scores", mode="before")
    @classmethod
    def _clean_scores(cls, value: object) -> dict[str, int] | None:
        if value is None or not isinstance(value, dict):
            return None
        out: dict[str, int] = {}
        for k, v in value.items():
            key = str(k).strip()
            if key not in REVIEW_DIM_KEYS:
                continue
            try:
                score = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= score <= 5:
                out[key] = score
        return out or None


class CaseReviewOut(BaseModel):
    summary: str = ""
    issues: list[CaseReviewIssue] = Field(default_factory=list)
    cases: list[GeneratedCase] = Field(default_factory=list)
    dimension_scores: dict[str, int] | None = None

    @field_validator("dimension_scores", mode="before")
    @classmethod
    def _clean_scores(cls, value: object) -> dict[str, int] | None:
        return CaseReviewResult._clean_scores(value)


class AgentArtifacts(BaseModel):
    requirement_analysis: RequirementAnalysisOut | None = None
    test_points: list[TestPointOut] = Field(default_factory=list)
    review: CaseReviewResult | None = None
