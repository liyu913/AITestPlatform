import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base, TimestampMixin


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    body_md: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(64), default="draft")
    priority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analyzed_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    analysis_messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    analysis_pending_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan_draft_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_draft_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    plan_draft_messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Acceptance rules are the human-confirmed pass/fail bar used by case review and
    # by execution judgement (B2). Empty means "not specified yet".
    acceptance_rules: Mapped[str] = mapped_column(Text, default="")
    version_no: Mapped[int] = mapped_column(Integer, default=1)

    project = relationship("Project")
    attachments = relationship(
        "RequirementAttachment",
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="RequirementAttachment.id",
    )
    versions = relationship(
        "RequirementVersion",
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="RequirementVersion.version_no",
    )


class RequirementAttachment(Base, TimestampMixin):
    __tablename__ = "requirement_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    requirement = relationship("Requirement", back_populates="attachments")


class RequirementVersion(Base, TimestampMixin):
    """Immutable snapshot of a requirement so old executions keep their source."""

    __tablename__ = "requirement_versions"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version_no", name="uq_requirement_version_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    change_source: Mapped[str] = mapped_column(String(32), default="edit")
    change_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    requirement = relationship("Requirement", back_populates="versions")
