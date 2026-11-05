import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base, TimestampMixin

# Failure classification agreed for the first version.
DEFECT_CATEGORIES = ("product", "environment", "data", "script", "rule_pending", "ux")

# Lifecycle: open → in_progress → resolved → closed, with reopened on a failed retest.
DEFECT_STATUSES = ("open", "in_progress", "resolved", "closed", "reopened")


class Defect(Base, TimestampMixin):
    __tablename__ = "defects"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "failure_source_type",
            "failure_source_id",
            name="uq_defect_failure_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(64), default="P2")
    status: Mapped[str] = mapped_column(String(64), default="open")
    category: Mapped[str] = mapped_column(String(32), default="product", index=True)
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cycle_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cycle_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Where the failure came from, so duplicate submissions can be detected.
    failure_source_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    failure_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_result: Mapped[str] = mapped_column(Text, default="")
    actual_result: Mapped[str] = mapped_column(Text, default="")
    environment: Mapped[str | None] = mapped_column(String(256), nullable=True)
    found_build: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fix_build: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Retest of the original problem; closing requires a passed one for fix_build.
    verified_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verification_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verification_note: Mapped[str] = mapped_column(Text, default="")
    regression_cycle_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    closed_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
    history = relationship(
        "DefectStatusHistory",
        back_populates="defect",
        cascade="all, delete-orphan",
        order_by="DefectStatusHistory.id",
    )


class DefectStatusHistory(Base, TimestampMixin):
    __tablename__ = "defect_status_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    defect_id: Mapped[int] = mapped_column(ForeignKey("defects.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str] = mapped_column(String(64), default="")
    to_status: Mapped[str] = mapped_column(String(64), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    defect = relationship("Defect", back_populates="history")


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
