from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SystemLlmProfile(Base, TimestampMixin):
    """系统级 LLM 配置档案；全局仅一条 is_active=true 生效。"""

    __tablename__ = "system_llm_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    api_base: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    allowed_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class SystemMidsceneProfile(Base, TimestampMixin):
    """Midscene 视觉模型；全局仅一条 is_active=true 生效。"""

    __tablename__ = "system_midscene_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    api_base: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    model_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class SystemEmbeddingProfile(Base, TimestampMixin):
    """系统级 Embedding 配置档案；全局仅一条 is_active=true 生效。"""

    __tablename__ = "system_embedding_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    api_base: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
