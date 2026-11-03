from datetime import datetime

from pydantic import BaseModel, Field


class ProjectModuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: int | None = None
    description: str = Field(default="", max_length=65535)
    sort_order: int = Field(default=0, ge=0, le=1_000_000)


class ProjectModuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: int | None = None
    description: str | None = Field(default=None, max_length=65535)
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)


class ProjectModuleOut(BaseModel):
    id: int
    project_id: int
    parent_id: int | None
    name: str
    description: str
    sort_order: int
    creator_id: int | None
    created_at: datetime
    updated_at: datetime


class ProjectModuleTreeNode(BaseModel):
    id: int
    parent_id: int | None
    name: str
    description: str
    sort_order: int
    children: list["ProjectModuleTreeNode"] = Field(default_factory=list)
