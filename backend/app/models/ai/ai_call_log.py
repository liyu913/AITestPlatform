import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base


class AiCallLog(Base):
    """一次大模型或向量调用的审计记录。正文在写入时已截断。"""

    __tablename__ = "ai_call_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        BeijingDateTime, server_default=func.now(), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scene: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    skill_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_skill_versions.id", ondelete="SET NULL"), nullable=True
    )
    binding_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_config: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
