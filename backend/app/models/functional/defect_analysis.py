"""Defect analysis records and submission attempts (R4)."""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base, TimestampMixin

ANALYSIS_CATEGORIES = ("product", "environment", "data", "script", "pending")
ANALYSIS_STATUSES = ("draft", "confirmed", "dismissed", "converted")
SUBMISSION_TARGETS = ("internal", "external")
SUBMISSION_STATUSES = ("success", "failed")


class DefectAnalysis(Base, TimestampMixin):
    __tablename__ = "defect_analyses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    defect_id: Mapped[int | None] = mapped_column(
        ForeignKey("defects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(512))
    observed: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    defect = relationship("Defect")


class DefectSubmission(Base, TimestampMixin):
    __tablename__ = "defect_submissions"
    __table_args__ = (
        UniqueConstraint("project_id", "defect_id", "request_id", name="uq_defect_submission_req"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    defect_id: Mapped[int] = mapped_column(
        ForeignKey("defects.id", ondelete="CASCADE"), index=True
    )
    target: Mapped[str] = mapped_column(String(32), default="internal")
    connector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="success", index=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    submitted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = [
    "ANALYSIS_CATEGORIES",
    "ANALYSIS_STATUSES",
    "SUBMISSION_STATUSES",
    "SUBMISSION_TARGETS",
    "DefectAnalysis",
    "DefectSubmission",
]
