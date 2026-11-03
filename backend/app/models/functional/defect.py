from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Defect(Base, TimestampMixin):
    __tablename__ = "defects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(64), default="P2")
    status: Mapped[str] = mapped_column(String(64), default="open")
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cycle_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cycle_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    external_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    attachments = relationship(
        "DefectAttachment",
        back_populates="defect",
        cascade="all, delete-orphan",
        order_by="DefectAttachment.id",
    )


class DefectAttachment(Base, TimestampMixin):
    __tablename__ = "defect_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    defect_id: Mapped[int] = mapped_column(ForeignKey("defects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    defect = relationship("Defect", back_populates="attachments")
