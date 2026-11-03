from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class IndexChunk(Base, TimestampMixin):
    __tablename__ = "index_chunks"
    __table_args__ = (
        Index("ix_index_chunks_project_id", "project_id"),
        Index("ix_index_chunks_source", "source_type", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int] = mapped_column(BigInteger)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str] = mapped_column(String(64))
    milvus_primary_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
