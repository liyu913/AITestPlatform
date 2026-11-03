from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

DEFAULT_SESSION_TITLE = "新对话"
DEFAULT_GENERATE_MODE = "fast"


class AiGenerationSession(Base, TimestampMixin):
    __tablename__ = "ai_generation_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default=DEFAULT_SESSION_TITLE)
    messages_json: Mapped[list] = mapped_column(JSON, nullable=False)
    drafts_json: Mapped[list] = mapped_column(JSON, nullable=False)
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True
    )
    knowledge_library_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_libraries.id", ondelete="SET NULL"), nullable=True
    )
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generate_mode: Mapped[str] = mapped_column(String(16), default=DEFAULT_GENERATE_MODE)
    artifacts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
