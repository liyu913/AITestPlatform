import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.identity.auth import Username

ApprovalStatus = Literal["pending", "approved", "rejected"]


class UserCreateRequest(BaseModel):
    username: Username
    password: str = Field(min_length=1)
    full_name: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
    approval_status: ApprovalStatus = "approved"
    created_at: datetime.datetime | None = None

    model_config = {"from_attributes": True}


class UserAdminPatch(BaseModel):
    is_active: bool | None = None


class UserAdminListOut(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    page_size: int
