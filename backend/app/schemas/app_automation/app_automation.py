from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CaseStatus = Literal["draft", "ready"]
AppPlatform = Literal["android", "ios"]
LocatorStrategy = Literal[
    "id",
    "accessibility_id",
    "xpath",
    "class_name",
    "android_uiautomator",
    "ios_predicate",
    "ios_class_chain",
]
AppAction = Literal[
    "launch",
    "tap",
    "type",
    "clear",
    "swipe",
    "back",
    "long_press",
    "wait",
    "assert_visible",
    "assert_text",
    "screenshot",
    "hide_keyboard",
]


class AppAccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    variables: dict[str, str] = Field(default_factory=dict)


class AppStepIn(BaseModel):
    action: AppAction
    strategy: LocatorStrategy | None = None
    selector: str | None = Field(default=None, max_length=1024)
    element: str | None = Field(default=None, max_length=256, description="PageObject 引用 page.name")
    value: str | None = Field(default=None, max_length=65535)
    assert_: str | None = Field(default=None, alias="assert", max_length=65535)
    timeout_ms: int | None = Field(default=None, ge=100, le=120000)
    retries: int | None = Field(default=None, ge=0, le=5)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _check_required(self) -> "AppStepIn":
        action = self.action
        has_sel = bool((self.selector or "").strip() or (self.element or "").strip())
        if action in ("tap", "clear", "long_press", "assert_visible"):
            if not has_sel:
                raise ValueError(f"{action} 需要 selector 或 element")
        elif action == "type":
            if not has_sel:
                raise ValueError("type 需要 selector 或 element")
            if self.value is None:
                raise ValueError("type 需要 value")
        elif action == "assert_text":
            if not has_sel:
                raise ValueError("assert_text 需要 selector 或 element")
            if self.assert_ is None and self.value is None:
                raise ValueError("assert_text 需要 assert 或 value")
        elif action == "swipe":
            if not (self.value or "").strip():
                raise ValueError("swipe 需要 value（left|right|up|down 或 x1,y1,x2,y2）")
        elif action == "wait":
            state = (self.assert_ or self.value or "visible").strip()
            if state not in ("visible", "invisible", "gone"):
                raise ValueError("wait 的 assert/value 须为 visible|invisible|gone")
            if not has_sel:
                raise ValueError("wait 需要 selector 或 element")
        return self


def _validate_appium_url(v: str) -> str:
    from app.services.app_automation.app_url_guard import assert_safe_appium_url

    return assert_safe_appium_url(v)


class AppEnvCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    platform: AppPlatform = "android"
    appium_server_url: str = Field(default="http://127.0.0.1:4723", max_length=1024)
    device_name: str = Field(default="Android Emulator", max_length=255)
    udid: str = Field(default="", max_length=255)
    platform_version: str = Field(default="", max_length=64)
    automation_name: str = Field(default="UiAutomator2", max_length=64)
    app_package: str = Field(default="", max_length=255)
    app_activity: str = Field(default="", max_length=255)
    bundle_id: str = Field(default="", max_length=255)
    app_path: str = Field(default="", max_length=1024)
    no_reset: bool = True
    full_reset: bool = False
    caps: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    accounts: list[AppAccountIn] = Field(default_factory=list)
    queue_name: str = Field(default="app", max_length=64)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    default_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    step_retries: int = Field(default=1, ge=0, le=5)
    is_default: bool = False
    device_tags: list[str] = Field(default_factory=list)
    use_device_pool: bool = True

    @field_validator("appium_server_url")
    @classmethod
    def _appium_url(cls, v: str) -> str:
        return _validate_appium_url(v)

    @model_validator(mode="after")
    def _platform_fields(self) -> "AppEnvCreate":
        if self.platform == "android":
            if not (self.app_package or "").strip() and not (self.app_path or "").strip():
                raise ValueError("Android 环境需要 app_package 或 app_path")
        elif self.platform == "ios":
            if not (self.bundle_id or "").strip() and not (self.app_path or "").strip():
                raise ValueError("iOS 环境需要 bundle_id 或 app_path")
        return self


class AppEnvUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    platform: AppPlatform | None = None
    appium_server_url: str | None = Field(default=None, max_length=1024)
    device_name: str | None = Field(default=None, max_length=255)
    udid: str | None = Field(default=None, max_length=255)
    platform_version: str | None = Field(default=None, max_length=64)
    automation_name: str | None = Field(default=None, max_length=64)
    app_package: str | None = Field(default=None, max_length=255)
    app_activity: str | None = Field(default=None, max_length=255)
    bundle_id: str | None = Field(default=None, max_length=255)
    app_path: str | None = Field(default=None, max_length=1024)
    no_reset: bool | None = None
    full_reset: bool | None = None
    caps: dict[str, Any] | None = None
    variables: dict[str, str] | None = None
    secrets: dict[str, str] | None = None
    accounts: list[AppAccountIn] | None = None
    queue_name: str | None = Field(default=None, max_length=64)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    default_timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    step_retries: int | None = Field(default=None, ge=0, le=5)
    is_default: bool | None = None
    device_tags: list[str] | None = None
    use_device_pool: bool | None = None

    @field_validator("appium_server_url")
    @classmethod
    def _appium_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_appium_url(v)


class AppEnvOut(BaseModel):
    id: int
    project_id: int
    name: str
    platform: str
    appium_server_url: str
    device_name: str
    udid: str
    platform_version: str
    automation_name: str
    app_package: str
    app_activity: str
    bundle_id: str
    app_path: str
    no_reset: bool
    full_reset: bool
    caps: dict[str, Any]
    variables: dict[str, str]
    secret_keys: list[str]
    accounts: list[dict[str, Any]]
    queue_name: str
    max_concurrency: int
    default_timeout_ms: int
    step_retries: int
    is_default: bool
    device_tags: list[str] = Field(default_factory=list)
    use_device_pool: bool = True
    created_at: datetime
    updated_at: datetime


DeviceStatus = Literal["online", "busy", "offline", "disabled"]


class AppDeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    platform: AppPlatform = "android"
    udid: str = Field(default="", max_length=255)
    device_name: str = Field(default="", max_length=255)
    platform_version: str = Field(default="", max_length=64)
    appium_server_url: str = Field(default="", max_length=1024)
    automation_name: str = Field(default="", max_length=64)
    tags: list[str] = Field(default_factory=list)
    status: DeviceStatus = "offline"
    worker_id: str = Field(default="", max_length=128)
    notes: str = Field(default="", max_length=65535)

    @field_validator("appium_server_url")
    @classmethod
    def _appium_url(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            return ""
        return _validate_appium_url(s)


class AppDeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    platform: AppPlatform | None = None
    udid: str | None = Field(default=None, max_length=255)
    device_name: str | None = Field(default=None, max_length=255)
    platform_version: str | None = Field(default=None, max_length=64)
    appium_server_url: str | None = Field(default=None, max_length=1024)
    automation_name: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = None
    status: DeviceStatus | None = None
    worker_id: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=65535)

    @field_validator("appium_server_url")
    @classmethod
    def _appium_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        s = v.strip()
        if not s:
            return ""
        return _validate_appium_url(s)


class AppDeviceOut(BaseModel):
    id: int
    project_id: int
    name: str
    platform: str
    udid: str
    device_name: str
    platform_version: str
    appium_server_url: str
    automation_name: str
    tags: list[str]
    status: str
    worker_id: str
    last_heartbeat_at: str | None
    current_run_id: int | None
    notes: str
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class AppDeviceHeartbeatRequest(BaseModel):
    worker_id: str | None = Field(default=None, max_length=128)
    status: Literal["online", "offline", "disabled"] | None = None


class AppDeviceScanRequest(BaseModel):
    mark_missing_offline: bool = True


class AppDeviceScanOut(BaseModel):
    scanned: int
    ready: int
    created: int
    updated: int
    online: int
    skipped_busy: int
    marked_offline: int
    devices: list[AppDeviceOut]
    raw: list[dict[str, Any]]


class AppElementCreate(BaseModel):
    page_name: str = Field(default="", max_length=128)
    name: str = Field(min_length=1, max_length=128)
    strategy: LocatorStrategy = "id"
    selector: str = Field(min_length=1, max_length=1024)
    description: str = Field(default="", max_length=65535)


class AppElementUpdate(BaseModel):
    page_name: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    strategy: LocatorStrategy | None = None
    selector: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = Field(default=None, max_length=65535)


class AppElementOut(BaseModel):
    id: int
    project_id: int
    page_name: str
    name: str
    strategy: str
    selector: str
    description: str
    creator_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AppCaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=65535)
    steps: list[AppStepIn] = Field(default_factory=list)
    manual_case_id: int | None = None
    status: CaseStatus = "draft"
    tags: list[str] | None = None


class AppCaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=65535)
    steps: list[AppStepIn] | None = None
    manual_case_id: int | None = None
    status: CaseStatus | None = None
    tags: list[str] | None = None


class AppCaseOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    steps: list[dict[str, Any]]
    manual_case_id: int | None
    status: str
    tags_json: list[str] | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class AppCaseRunRequest(BaseModel):
    env_id: int | None = None
    account_name: str | None = None
    device_id: int | None = None
    device_tags: list[str] | None = None
    sync: bool = False


class AppRunOut(BaseModel):
    id: int
    project_id: int
    trigger_type: str
    target_id: int
    env_id: int | None
    device_id: int | None = None
    device_tags_json: list[str] | None = None
    device_snapshot_json: dict[str, Any] | None = None
    platform: str = "android"
    account_name: str | None = None
    queue_name: str = "app"
    status: str
    summary_json: dict[str, Any] | None
    triggered_by: int | None
    celery_task_id: str | None
    started_at: str | None
    finished_at: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AppRunStepOut(BaseModel):
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


class AppTrendPoint(BaseModel):
    day: str
    total: int
    passed: int
    failed: int
    error: int


class AppTrendOut(BaseModel):
    points: list[AppTrendPoint]
    totals: dict[str, int]
