from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class KnowledgeLibrary(Base, TimestampMixin):
    __tablename__ = "knowledge_libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_mode: Mapped[str] = mapped_column(String(32), default="window", server_default="window")
    chunk_window: Mapped[int] = mapped_column(Integer, default=400, server_default="400")
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=80, server_default="80")
    reindex_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    documents = relationship("KnowledgeDocument", back_populates="library")
