from datetime import datetime

from pydantic import BaseModel, Field, field_validator

CASE_TYPES = ("功能", "边界", "异常", "权限")


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
    status: str = Field(default="draft", max_length=64)
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
    status: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = None
    steps: list[TestCaseStepIn] | None = None

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
    status: str
    tags_json: list[str] | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime
    steps: list[TestCaseStepOut]
    linked_ui_case_count: int = 0

    model_config = {"from_attributes": True}
