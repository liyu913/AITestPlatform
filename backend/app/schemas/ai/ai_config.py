from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SkillOut(BaseModel):
    id: int
    project_id: int | None = None
    skill_key: str
    name: str
    action: str
    version: int
    description: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    tools_json: list | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillCreate(BaseModel):
    skill_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=32)
    version: int = Field(default=1, ge=1)
    description: str | None = Field(default=None, max_length=65535)
    input_schema: dict | None = None
    output_schema: dict | None = None
    resource_text: str | None = Field(default=None, max_length=65535)
    tools_json: list | None = None
    project_id: int | None = None
    enabled: bool = True


class ModelProfileRef(BaseModel):
    id: int
    name: str
    model: str | None = None
    is_active: bool = False


class BindingIn(BaseModel):
    mode: str = Field(default="model", max_length=16)
    skill_version_id: int | None = None
    model_profile_id: int | None = None
    params: dict[str, Any] | None = None


class BindingOut(BaseModel):
    id: int
    project_id: int
    action: str
    mode: str
    skill_version_id: int | None = None
    model_profile_id: int | None = None
    params: dict[str, Any] | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class DefaultsIn(BaseModel):
    default_model_profile_id: int | None = None


class DefaultsOut(BaseModel):
    project_id: int
    default_model_profile_id: int | None = None

    model_config = {"from_attributes": True}


class ResolvedOut(BaseModel):
    action: str
    mode: str
    source: str
    binding_id: int | None = None
    skill_version_id: int | None = None
    skill_key: str | None = None
    skill_name: str | None = None
    skill_version: int | None = None
    model_profile_id: int | None = None
    model_name: str | None = None
    api_base: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AiConfigOverview(BaseModel):
    actions: list[str]
    bindings: list[BindingOut]
    defaults: DefaultsOut
    skills: list[SkillOut]
    models: list[ModelProfileRef]


class SkillUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=65535)
