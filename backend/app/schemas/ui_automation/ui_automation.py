from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CaseStatus = Literal["draft", "ready"]
BrowserName = Literal["chromium", "firefox", "webkit"]
TokenInject = Literal["none", "header", "local_storage"]
AuthMode = Literal["none", "account", "token"]
UiAction = Literal[
    "goto",
    "click",
    "fill",
    "select_option",
    "press",
    "hover",
    "wait_for",
    "assert_visible",
    "assert_text",
    "assert_url",
    "screenshot",
]


class UiAccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    variables: dict[str, str] = Field(default_factory=dict)


class UiAccountOut(BaseModel):
    name: str
    variables: dict[str, str] = Field(default_factory=dict)
    has_storage_state: bool = False


class UiStepIn(BaseModel):
    action: UiAction
    selector: str | None = Field(default=None, max_length=1024)
    element: str | None = Field(default=None, max_length=256, description="PageObject 引用 page.name")
    value: str | None = Field(default=None, max_length=65535)
    assert_: str | None = Field(default=None, alias="assert", max_length=65535)
    timeout_ms: int | None = Field(default=None, ge=100, le=120000)
    retries: int | None = Field(default=None, ge=0, le=5)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _check_required(self) -> "UiStepIn":
        action = self.action
        has_sel = bool((self.selector or "").strip() or (self.element or "").strip())
        if action == "goto":
            if not (self.value or "").strip():
                raise ValueError("goto 需要 value（相对路径，以 / 开头）")
            path = (self.value or "").strip()
            if not path.startswith("/"):
                raise ValueError("goto 的 value 必须以 / 开头")
            if "://" in path or path.startswith("//"):
                raise ValueError("goto 的 value 不能是绝对 URL")
        elif action in ("click", "hover", "assert_visible"):
            if not has_sel:
                raise ValueError(f"{action} 需要 selector 或 element")
        elif action in ("fill", "select_option", "press"):
            if not has_sel:
                raise ValueError(f"{action} 需要 selector 或 element")
            if self.value is None or str(self.value) == "":
                raise ValueError(f"{action} 需要 value")
        elif action == "wait_for":
            state = (self.assert_ or self.value or "visible").strip()
            if state not in ("visible", "hidden", "url", "networkidle", "load", "domcontentloaded"):
                raise ValueError(
                    "wait_for 的 assert/value 须为 visible|hidden|url|networkidle|load|domcontentloaded"
                )
            if state in ("visible", "hidden") and not has_sel:
                raise ValueError("wait_for visible/hidden 需要 selector 或 element")
            if state == "url" and not (self.value or "").strip():
                raise ValueError("wait_for url 需要 value")
        elif action == "assert_text":
            if not has_sel:
                raise ValueError("assert_text 需要 selector 或 element")
            if self.assert_ is None and self.value is None:
                raise ValueError("assert_text 需要 assert 或 value")
        elif action == "assert_url":
            if not (self.assert_ or self.value or "").strip():
                raise ValueError("assert_url 需要 assert 或 value")
        return self


def _validate_base_url(v: str) -> str:
    s = v.strip().rstrip("/")
    if not (s.startswith("http://") or s.startswith("https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    return s


class UiEnvCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=1024)
    browser: BrowserName = "chromium"
    browsers: list[BrowserName] = Field(default_factory=lambda: ["chromium"])
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1280, "height": 720})
    variables: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    accounts: list[UiAccountIn] = Field(default_factory=list)
    queue_name: str = Field(default="ui", max_length=64)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    default_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    step_retries: int = Field(default=1, ge=0, le=5)
    is_default: bool = False
    token_inject: TokenInject = "none"
    token_storage_key: str | None = Field(default=None, max_length=128)
    inject_headers: dict[str, str] = Field(default_factory=dict)
    inject_local_storage: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        return _validate_base_url(v)

    @field_validator("token_storage_key")
    @classmethod
    def _token_key(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        return s or None


class UiEnvUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    browser: BrowserName | None = None
    browsers: list[BrowserName] | None = None
    viewport: dict[str, int] | None = None
    variables: dict[str, str] | None = None
    secrets: dict[str, str] | None = None
    accounts: list[UiAccountIn] | None = None
    queue_name: str | None = Field(default=None, max_length=64)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    default_timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    step_retries: int | None = Field(default=None, ge=0, le=5)
    is_default: bool | None = None
    token_inject: TokenInject | None = None
    token_storage_key: str | None = Field(default=None, max_length=128)
    inject_headers: dict[str, str] | None = None
    inject_local_storage: dict[str, str] | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_base_url(v)

    @field_validator("token_storage_key")
    @classmethod
    def _token_key(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        return s or None


class UiEnvOut(BaseModel):
    id: int
    project_id: int
    name: str
    base_url: str
    browser: str
    browsers: list[str]
    viewport: dict[str, Any]
    variables: dict[str, str]
    secret_keys: list[str]
    accounts: list[UiAccountOut]
    token_inject: str = "none"
    token_storage_key: str | None = None
    inject_headers: dict[str, str] = Field(default_factory=dict)
    inject_local_storage: dict[str, str] = Field(default_factory=dict)
    queue_name: str
    max_concurrency: int
    default_timeout_ms: int
    step_retries: int
    is_default: bool
    created_at: datetime
    updated_at: datetime


class UiElementCreate(BaseModel):
    page_name: str = Field(default="", max_length=128)
    name: str = Field(min_length=1, max_length=128)
    selector: str = Field(min_length=1, max_length=1024)
    description: str = Field(default="", max_length=65535)


class UiElementUpdate(BaseModel):
    page_name: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    selector: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = Field(default=None, max_length=65535)


class UiElementOut(BaseModel):
    id: int
    project_id: int
    page_name: str
    name: str
    selector: str
    description: str
    creator_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _normalize_auth_mode(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in ("none", "account", "token"):
        return raw
    return None


def _as_str_vars(value: object) -> dict[str, str]:
    if value is None or not isinstance(value, dict):
        return {}
    return {str(k).strip(): ("" if v is None else str(v)) for k, v in value.items() if str(k).strip()}


class UiCaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    script: str = Field(default="", max_length=200000)
    steps: list[UiStepIn] = Field(default_factory=list, description="录制扩展上传的步骤，服务端编译为 script")
    variables: dict[str, str] = Field(default_factory=dict)
    auth_mode: AuthMode | None = None
    account_name: str | None = Field(default=None, max_length=128)
    manual_case_id: int | None = None
    status: CaseStatus = "draft"
    tags: list[str] | None = None

    @field_validator("variables", mode="before")
    @classmethod
    def _variables(cls, value: object) -> dict[str, str]:
        return _as_str_vars(value)

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _auth_mode(cls, value: object) -> str | None:
        return _normalize_auth_mode(value)

    @field_validator("account_name", mode="before")
    @classmethod
    def _account_name(cls, value: object) -> str | None:
        raw = str(value or "").strip()
        return raw or None


class UiCaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    script: str | None = Field(default=None, max_length=200000)
    steps: list[UiStepIn] | None = Field(default=None, description="录制扩展上传的步骤，服务端编译为 script")
    variables: dict[str, str] | None = None
    auth_mode: AuthMode | None = None
    account_name: str | None = Field(default=None, max_length=128)
    manual_case_id: int | None = None
    status: CaseStatus | None = None
    tags: list[str] | None = None

    @field_validator("variables", mode="before")
    @classmethod
    def _variables(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        return _as_str_vars(value)

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _auth_mode(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_auth_mode(value)

    @field_validator("account_name", mode="before")
    @classmethod
    def _account_name(cls, value: object) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        return raw or None


class UiCaseOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    script: str = ""
    steps: list[dict[str, Any]]
    variables: dict[str, str] = Field(default_factory=dict)
    auth_mode: str | None = None
    account_name: str | None = None
    manual_case_id: int | None
    status: str
    tags_json: list[str] | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class UiCaseRunRequest(BaseModel):
    env_id: int | None = None
    browser: BrowserName | None = None
    account_name: str | None = None
    auth_mode: AuthMode | None = None
    variables: dict[str, str] | None = None
    headless: bool = True
    sync: bool = False
    cycle_item_id: int | None = None

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _auth_mode(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_auth_mode(value)

    @field_validator("variables", mode="before")
    @classmethod
    def _variables(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        return _as_str_vars(value)

    @field_validator("account_name", mode="before")
    @classmethod
    def _account_name(cls, value: object) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        return raw or None


class UiSuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    case_ids: list[int] = Field(default_factory=list)
    stop_on_fail: bool = True
    env_id: int | None = None
    browsers: list[BrowserName] = Field(default_factory=list)


class UiSuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    case_ids: list[int] | None = None
    stop_on_fail: bool | None = None
    env_id: int | None = None
    browsers: list[BrowserName] | None = None


class UiSuiteOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    case_ids: list[int]
    stop_on_fail: bool
    env_id: int | None
    browsers: list[str]
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class UiSuiteRunRequest(BaseModel):
    env_id: int | None = None
    browser: BrowserName | None = None
    account_name: str | None = None
    auth_mode: AuthMode | None = None
    variables: dict[str, str] | None = None
    headless: bool = True
    sync: bool = False
    cycle_item_id: int | None = None

    @field_validator("auth_mode", mode="before")
    @classmethod
    def _auth_mode(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_auth_mode(value)

    @field_validator("variables", mode="before")
    @classmethod
    def _variables(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        return _as_str_vars(value)

    @field_validator("account_name", mode="before")
    @classmethod
    def _account_name(cls, value: object) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        return raw or None


class UiScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    suite_id: int
    env_id: int | None = None
    cron_expr: str = Field(default="0 * * * *", min_length=1, max_length=128)
    enabled: bool = True


class UiScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    suite_id: int | None = None
    env_id: int | None = None
    cron_expr: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None


class UiScheduleOut(BaseModel):
    id: int
    project_id: int
    name: str
    suite_id: int
    env_id: int | None
    cron_expr: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class UiCiTriggerRequest(BaseModel):
    suite_id: int
    env_id: int | None = None
    browser: BrowserName | None = None
    account_name: str | None = None
    token: str | None = Field(default=None, description="可选 CI token，与 JWT 二选一由调用方鉴权")


class UiRunOut(BaseModel):
    id: int
    project_id: int
    trigger_type: str
    target_id: int
    env_id: int | None
    browser: str = "chromium"
    headless: bool = True
    account_name: str | None = None
    queue_name: str = "ui"
    status: str
    summary_json: dict[str, Any] | None
    triggered_by: int | None
    celery_task_id: str | None
    started_at: str | None
    finished_at: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UiRunStepOut(BaseModel):
    id: int
    run_id: int
    project_id: int
    order: int
    case_id: int | None = None
    case_name: str = ""
    action: str
    selector: str
    status: str
    error_message: str
    duration_ms: int | None
    screenshot_key: str | None
    detail_json: dict[str, Any] | None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UiRunFixPlan(BaseModel):
    summary: str = ""
    likely_causes: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_script: str = ""


class UiRunFixConfirm(BaseModel):
    action: Literal["confirm", "cancel"]
    apply_patch: bool = False
    rerun: bool = False


class UiRunFixOut(BaseModel):
    id: str
    run_id: int
    case_id: int | None = None
    status: str
    plan: UiRunFixPlan | None = None
    current_script: str = ""
    error_message: str = ""
    patch_message: str = ""
    rerun_id: int | None = None
    explore_session_id: int | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class UiTrendPoint(BaseModel):
    day: str
    total: int
    passed: int
    failed: int
    error: int


class UiTrendOut(BaseModel):
    points: list[UiTrendPoint]
    totals: dict[str, int]


class UiRecordingStart(BaseModel):
    env_id: int | None = None


class UiRecordingOut(BaseModel):
    id: str
    status: Literal["starting", "recording", "stopped", "failed"]
    base_url: str = ""
    step_count: int = 0
    script: str = ""
    suggested_name: str = ""
    error_message: str = ""
