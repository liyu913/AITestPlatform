from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ApiSpecOut(BaseModel):
    id: int
    project_id: int
    name: str
    source_type: str
    summary_json: dict[str, Any] | None
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class ApiSpecEndpointOut(BaseModel):
    id: int
    spec_id: int
    project_id: int
    method: str
    path: str
    operation_id: str | None
    summary: str | None
    tags_json: list[str] | None
    request_sample_json: dict[str, Any] | None


class ApiSpecImportBody(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=5_000_000)


class ApiSpecImportToCasesBody(BaseModel):
    endpoint_ids: list[int] = Field(min_length=1)
    status: str = "draft"


class ApiScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    suite_id: int
    env_id: int | None = None
    cron_expr: str = Field(default="0 * * * *", max_length=128)
    enabled: bool = True


class ApiScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    suite_id: int | None = None
    env_id: int | None = None
    cron_expr: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None


class ApiScheduleOut(BaseModel):
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


class PostmanImportBody(BaseModel):
    content: str = Field(min_length=1, max_length=5_000_000)
    create_cases: bool = True


class PostmanExportBody(BaseModel):
    case_ids: list[int] | None = None
    name: str = "Raina Export"
    base_url: str = "{{base_url}}"


class ApiDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    module: str = Field(default="", max_length=255)
    module_id: int | None = None
    description: str = Field(default="", max_length=65535)
    method: str = Field(default="GET", max_length=16)
    path: str = Field(min_length=1, max_length=1024)
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_type: str = Field(default="none", max_length=32)
    body: str = Field(default="", max_length=65535)
    tags: list[str] | None = None
    status: str = Field(default="active", max_length=32)

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        s = v.strip()
        if not s.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        if "://" in s:
            raise ValueError("path 不能包含完整 URL")
        return s


class ApiDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    module: str | None = Field(default=None, max_length=255)
    module_id: int | None = None
    description: str | None = Field(default=None, max_length=65535)
    method: str | None = Field(default=None, max_length=16)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    query: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    body_type: str | None = Field(default=None, max_length=32)
    body: str | None = Field(default=None, max_length=65535)
    tags: list[str] | None = None
    status: str | None = Field(default=None, max_length=32)

    @field_validator("method")
    @classmethod
    def _method(cls, v: str | None) -> str | None:
        return v.strip().upper() if v is not None else v

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


class ApiDefinitionOut(BaseModel):
    id: int
    project_id: int
    name: str
    module: str
    module_id: int | None = None
    description: str
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body_type: str
    body: str
    tags_json: list[str] | None
    status: str
    source: str
    operation_id: str | None
    spec_id: int | None
    spec_endpoint_id: int | None
    creator_id: int | None
    case_count: int = 0
    created_at: datetime
    updated_at: datetime


class ApiDefinitionSyncFromSpecBody(BaseModel):
    spec_id: int
    endpoint_ids: list[int] | None = None


class ApiDefinitionCreateCaseBody(BaseModel):
    name: str | None = Field(default=None, max_length=512)
    status: str = "draft"


class ApiDefinitionGenerateCasesBody(BaseModel):
    status: str = "draft"
    max_cases: int = Field(default=12, ge=1, le=20)
