from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.identity.auth import Username


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str | None
    owner_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListItemOut(ProjectOut):
    my_role: str


class MemberAdd(BaseModel):
    username: Username
    role: str = Field(pattern="^(admin|editor|viewer)$")


class MemberPatch(BaseModel):
    role: str = Field(pattern="^(admin|editor|viewer)$")


class MemberOut(BaseModel):
    user_id: int
    username: str
    role: str
