from typing import Annotated

from pydantic import BaseModel, Field

USERNAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$"
Username = Annotated[
    str,
    Field(
        min_length=3,
        max_length=32,
        pattern=USERNAME_PATTERN,
        description="3–32 位，字母开头，仅字母/数字/下划线",
    ),
]


class LoginRequest(BaseModel):
    username: Username
    password: str = Field(min_length=1)
    captcha_id: str | None = None
    captcha_code: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    username: Username
    password: str = Field(min_length=8)
    full_name: str | None = None
    captcha_id: str | None = None
    captcha_code: str | None = None


class RegisterOptionsOut(BaseModel):
    allowed: bool
    require_captcha: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    message: str
