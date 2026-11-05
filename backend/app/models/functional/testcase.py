import datetime

from sqlalchemy import (
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base, TimestampMixin


class TestCase(Base, TimestampMixin):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("project_id", "ai_draft_id", name="uq_test_cases_project_ai_draft"),)

    ai_draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    design_trace: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    precondition: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    overall_expected: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Legacy column kept for old readers/queries; it mirrors lifecycle_status and never
    # stores an execution result. See app/services/functional/testcase_lifecycle.py.
    status: Mapped[str] = mapped_column(String(64), default="draft")
    # Lifecycle: draft / pending_review / confirmed / archived.
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # Latest execution outcome; None means "never executed".
    last_result: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    last_result_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    # Formal-library pointer: the approved version currently published for reuse.
    published_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="SET NULL"), nullable=True
    )
    published_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    assigned_reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_due_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    archived_at: Mapped[datetime.datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    archived_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    steps = relationship(
        "TestCaseStep",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="TestCaseStep.order",
    )
    versions = relationship(
        "TestCaseVersion",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="TestCaseVersion.version_no",
        foreign_keys="TestCaseVersion.case_id",
    )
    reviews = relationship(
        "TestCaseReview",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="TestCaseReview.id",
    )


class TestCaseStep(Base, TimestampMixin):
    __tablename__ = "test_case_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)

    case = relationship("TestCase", back_populates="steps")


class TestCaseVersion(Base, TimestampMixin):
    """Immutable snapshot of a case at a given version.

    ``snapshot`` is intentionally plain JSON so that a later edit or archive never
    changes what an old run/report pointed at.
    """

    __tablename__ = "test_case_versions"
    __table_args__ = (UniqueConstraint("case_id", "version_no", name="uq_test_case_version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="draft")
    snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    change_source: Mapped[str] = mapped_column(String(32), default="edit")
    change_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    case = relationship(
        "TestCase", back_populates="versions", foreign_keys="TestCaseVersion.case_id"
    )


class TestCaseReview(Base, TimestampMixin):
    """Review decision bound to one case version."""

    __tablename__ = "test_case_reviews"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="SET NULL"), nullable=True
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    decision: Mapped[str] = mapped_column(String(16), default="approved")
    comment: Mapped[str] = mapped_column(Text, default="")
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    case = relationship("TestCase", back_populates="reviews")
