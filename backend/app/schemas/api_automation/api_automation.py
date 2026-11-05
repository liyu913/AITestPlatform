from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
BodyType = Literal["none", "json", "text"]
CaseStatus = Literal["draft", "ready"]


class ApiExtractorIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    from_: Literal["jsonpath"] = Field(default="jsonpath", alias="from")
    expr: str = Field(min_length=1, max_length=512)

    model_config = {"populate_by_name": True}


class ApiAssertionIn(BaseModel):
    type: Literal[
        "status",
        "jsonpath",
        "max_duration_ms",
        "contains",
        "not_contains",
        "regex",
        "header",
        "json_schema",
    ]
    expr: str | None = Field(default=None, max_length=512)
    op: Literal["eq", "exists", "contains", "regex"] = "eq"
    expected: Any = None


class ApiEnvCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=1024)
    variables: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        s = v.strip().rstrip("/")
        if not (s.startswith("http://") or s.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return s


class ApiEnvUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    variables: dict[str, str] | None = None
    secrets: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    is_default: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip().rstrip("/")
        if not (s.startswith("http://") or s.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return s


class ApiEnvOut(BaseModel):
    id: int
    project_id: int
    name: str
    base_url: str
    variables: dict[str, str]
    secret_keys: list[str]
    headers: dict[str, str]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ApiCaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    method: HttpMethod = "GET"
    path: str = Field(min_length=1, max_length=1024)
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_type: BodyType = "none"
    body: str = Field(default="", max_length=65535)
    extractors: list[ApiExtractorIn] = Field(default_factory=list)
    assertions: list[ApiAssertionIn] = Field(default_factory=list)
    timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    manual_case_id: int | None = None
    api_id: int | None = None
    status: CaseStatus = "draft"
    tags: list[str] | None = None
    pre_script: str = Field(default="", max_length=20000)
    post_script: str = Field(default="", max_length=20000)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        s = v.strip()
        if not s.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        if "://" in s:
            raise ValueError("path 不能包含完整 URL")
        return s


class ApiCaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    method: HttpMethod | None = None
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    query: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    body_type: BodyType | None = None
    body: str | None = Field(default=None, max_length=65535)
    extractors: list[ApiExtractorIn] | None = None
    assertions: list[ApiAssertionIn] | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    manual_case_id: int | None = None
    api_id: int | None = None
    status: CaseStatus | None = None
    tags: list[str] | None = None
    pre_script: str | None = Field(default=None, max_length=20000)
    post_script: str | None = Field(default=None, max_length=20000)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        if not s.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        if "://" in s:
            raise ValueError("path 不能包含完整 URL")
        return s


class ApiCaseOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body_type: str
    body: str
    extractors: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    timeout_ms: int
    manual_case_id: int | None
    api_id: int | None = None
    status: str
    tags_json: list[str] | None
    creator_id: int | None
    pre_script: str = ""
    post_script: str = ""
    created_at: datetime
    updated_at: datetime


class ApiCaseRunRequest(BaseModel):
    env_id: int | None = None
    sync: bool = False
    cycle_item_id: int | None = None


class ApiCaseBatchRunRequest(BaseModel):
    case_ids: list[int] = Field(default_factory=list)
    env_id: int | None = None
    sync: bool = False
    cycle_item_id: int | None = None


class ApiSuiteExtractorIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(default="jsonpath", max_length=32)
    expr: str = Field(default="", max_length=1024)


class ApiSuiteAssertionIn(BaseModel):
    type: Literal[
        "status",
        "jsonpath",
        "max_duration_ms",
        "contains",
        "not_contains",
        "regex",
        "header",
        "json_schema",
    ]
    expr: str | None = Field(default=None, max_length=512)
    op: Literal["eq", "exists", "contains", "regex"] = "eq"
    expected: Any = None


class ApiSuiteStepIn(BaseModel):
    """Suite step: prefer inline request payload; case_id kept for legacy suites."""

    id: str = Field(min_length=1, max_length=64)
    case_id: int | None = None
    when: dict | None = None
    on_pass: str = "end"
    on_fail: str = "end"
    rationale: str | None = None
    case_name: str | None = None
    definition_id: int | None = None
    definition_name: str | None = None
    # Inline executable request (scenario owns the payload)
    name: str | None = Field(default=None, max_length=512)
    method: HttpMethod | None = None
    path: str | None = Field(default=None, max_length=1024)
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_type: BodyType | None = None
    body: str = Field(default="", max_length=65535)
    assertions: list[ApiSuiteAssertionIn] = Field(default_factory=list)
    extractors: list[ApiSuiteExtractorIn] = Field(default_factory=list)
    timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    # Legacy override fields (merged when executing via case_id)
    extract_overrides: list[ApiSuiteExtractorIn] | None = None
    assert_overrides: list[ApiSuiteAssertionIn] | None = None


class ApiSuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    case_ids: list[int] = Field(default_factory=list)
    steps: list[ApiSuiteStepIn] | None = None
    stop_on_fail: bool = True
    env_id: int | None = None


class ApiSuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    case_ids: list[int] | None = None
    steps: list[ApiSuiteStepIn] | None = None
    stop_on_fail: bool | None = None
    env_id: int | None = None


class ApiSuiteOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    case_ids: list[int]
    steps: list[dict] = Field(default_factory=list)
    stop_on_fail: bool
    env_id: int | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class ApiSuiteRunRequest(BaseModel):
    env_id: int | None = None
    sync: bool = True
    cycle_item_id: int | None = None


class ScenarioEditorChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ScenarioEditorReviseIn(BaseModel):
    """Current editor steps (including unsaved edits) plus a natural-language change."""

    message: str = Field(min_length=1, max_length=4000)
    steps: list[ApiSuiteStepIn] = Field(default_factory=list)
    messages: list[ScenarioEditorChatTurn] = Field(default_factory=list, max_length=12)

    @field_validator("message")
    @classmethod
    def _message(cls, v: str) -> str:
        text = v.strip()
        if not text:
            raise ValueError("修改说明为空")
        return text


class ScenarioEditorReviseOut(BaseModel):
    summary: str
    warnings: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ApiDebugRequest(BaseModel):
    env_id: int | None = None
    method: HttpMethod = "GET"
    path: str = Field(min_length=1, max_length=1024)
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_type: BodyType = "none"
    body: str = Field(default="", max_length=65535)
    extractors: list[ApiExtractorIn] = Field(default_factory=list)
    assertions: list[ApiAssertionIn] = Field(default_factory=list)
    timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    pre_script: str = Field(default="", max_length=20000)
    post_script: str = Field(default="", max_length=20000)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        s = v.strip()
        if not s.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        if "://" in s:
            raise ValueError("path 不能包含完整 URL")
        return s


class ApiDebugResponse(BaseModel):
    status: str
    error_message: str = ""
    request_snapshot: dict[str, Any] | None = None
    response_status: int | None = None
    response_headers: dict[str, Any] | None = None
    response_body: str = ""
    duration_ms: int | None = None
    assertion_results: list[dict[str, Any]] = Field(default_factory=list)
    extract_results: dict[str, str] = Field(default_factory=dict)


class ApiRunOut(BaseModel):
    id: int
    project_id: int
    trigger_type: str
    target_id: int
    env_id: int | None
    status: str
    summary_json: dict[str, Any] | None
    triggered_by: int | None
    celery_task_id: str | None
    started_at: str | None
    finished_at: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApiRunStepOut(BaseModel):
    id: int
    run_id: int
    project_id: int
    order: int
    step_key: str | None = None
    case_id: int | None
    case_name: str
    request_snapshot_json: dict[str, Any] | None
    response_status: int | None
    response_headers_json: dict[str, Any] | None
    response_body_text: str = ""
    duration_ms: int | None
    assertion_results_json: list[Any] | None
    extract_results_json: dict[str, Any] | None
    status: str
    error_message: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
