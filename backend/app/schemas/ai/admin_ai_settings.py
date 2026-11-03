from pydantic import BaseModel, Field


class LlmProfileOut(BaseModel):
    id: int
    name: str
    api_base: str
    model: str
    allowed_models: str
    api_key: str = ""
    has_api_key: bool
    is_active: bool


class LlmProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    api_base: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=256)
    allowed_models: str | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    activate: bool = False


class LlmProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_base: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=256)
    allowed_models: str | None = None
    api_key: str | None = Field(default=None, max_length=4096)


class EmbeddingProfileOut(BaseModel):
    id: int
    name: str
    api_base: str
    model: str
    dim: int
    api_key: str = ""
    has_api_key: bool
    is_active: bool
    warning: str | None = None


class EmbeddingProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    api_base: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=256)
    dim: int | None = Field(default=None, ge=1, le=32768)
    api_key: str | None = Field(default=None, max_length=4096)
    activate: bool = False


class EmbeddingProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_base: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=256)
    dim: int | None = Field(default=None, ge=1, le=32768)
    api_key: str | None = Field(default=None, max_length=4096)


class MidsceneProfileOut(BaseModel):
    id: int
    name: str
    api_base: str
    model: str
    model_family: str
    api_key: str = ""
    has_api_key: bool
    is_active: bool


class MidsceneProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    api_base: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=256)
    model_family: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=4096)
    activate: bool = False


class MidsceneProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    api_base: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=256)
    model_family: str | None = Field(default=None, max_length=64)
    api_key: str | None = Field(default=None, max_length=4096)
