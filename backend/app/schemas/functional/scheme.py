from datetime import datetime

from pydantic import BaseModel, Field

SCHEME_STATUSES = ("draft", "active", "archived")
SCHEME_CONTENT_FIELDS = ("name", "scope", "strategy", "risks", "methods", "exit_criteria")


class SchemeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    scope: str = Field(default="", max_length=65535)
    strategy: str = Field(default="", max_length=65535)
    risks: str = Field(default="", max_length=65535)
    methods: str = Field(default="", max_length=65535)
    exit_criteria: str = Field(default="", max_length=65535)
    status: str = Field(default="draft", max_length=32)
    owner_id: int | None = None


class SchemeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    scope: str | None = Field(default=None, max_length=65535)
    strategy: str | None = Field(default=None, max_length=65535)
    risks: str | None = Field(default=None, max_length=65535)
    methods: str | None = Field(default=None, max_length=65535)
    exit_criteria: str | None = Field(default=None, max_length=65535)
    status: str | None = Field(default=None, max_length=32)
    owner_id: int | None = None
    change_note: str | None = Field(default=None, max_length=512)


class SchemeVersionOut(BaseModel):
    id: int
    scheme_id: int
    project_id: int
    version_no: int
    snapshot: dict | None = None
    change_note: str | None = None
    created_by_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SchemeOut(BaseModel):
    id: int
    project_id: int
    name: str
    status: str
    owner_id: int | None = None
    current_version_no: int
    plan_count: int = 0
    latest_snapshot: dict | None = None
    created_at: datetime
    updated_at: datetime


class SchemeDetailOut(SchemeOut):
    versions: list[SchemeVersionOut] = Field(default_factory=list)


class SchemeVersionCreate(BaseModel):
    change_note: str | None = Field(default=None, max_length=512)
