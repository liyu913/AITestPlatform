from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base, TimestampMixin


class TestPlan(Base, TimestampMixin):
    __tablename__ = "test_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    iteration: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    body_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheme_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_schemes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scheme_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_scheme_versions.id", ondelete="SET NULL"), nullable=True
    )
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    cycles = relationship(
        "TestCycle",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="TestCycle.id",
    )


class TestCycle(Base, TimestampMixin):
    """One execution batch of a plan.

    The configuration is a *snapshot*: target build, environment and credential
    reference are frozen when the batch starts, so later environment edits do
    not silently change what a historical batch ran against.
    """

    __tablename__ = "test_cycles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(64), default="draft")
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_build: Mapped[str | None] = mapped_column(String(128), nullable=True)
    env_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    env_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    env_snapshot_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_note: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    readiness_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    plan = relationship("TestPlan", back_populates="cycles")
    items = relationship(
        "TestCycleItem",
        back_populates="cycle",
        cascade="all, delete-orphan",
        order_by="TestCycleItem.id",
    )
    attempts = relationship(
        "TestCycleAttempt",
        back_populates="cycle",
        cascade="all, delete-orphan",
        order_by="TestCycleAttempt.id",
    )
    reports = relationship(
        "TestCycleReport",
        back_populates="cycle",
        cascade="all, delete-orphan",
        order_by="TestCycleReport.revision",
    )


class TestCycleItem(Base, TimestampMixin):
    """A planned execution unit.

    ``asset_type`` + ``asset_id`` identify which asset runs (functional case,
    API case/suite, UI case/suite). ``case_id`` stays for functional assets and
    for old rows/APIs; the two are kept consistent on write.
    """

    __tablename__ = "test_cycle_items"
    __table_args__ = (
        UniqueConstraint("cycle_id", "case_id", name="uq_cycle_case"),
        UniqueConstraint("cycle_id", "asset_type", "asset_id", name="uq_cycle_asset"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("test_cycles.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    asset_type: Mapped[str] = mapped_column(String(32), default="functional", index=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    result: Mapped[str] = mapped_column(String(64), default="untested")
    actual_result: Mapped[str] = mapped_column(Text, default="")
    executed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    # Latest execution adapter link (which run produced ``result``).
    last_run_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0)

    cycle = relationship("TestCycle", back_populates="items")
    attachments = relationship(
        "ExecutionAttachment",
        back_populates="cycle_item",
        cascade="all, delete-orphan",
        order_by="ExecutionAttachment.id",
    )
    attempts = relationship(
        "TestCycleAttempt",
        back_populates="cycle_item",
        cascade="all, delete-orphan",
        order_by="TestCycleAttempt.attempt_no",
    )


class TestCycleAttempt(Base, TimestampMixin):
    """One execution attempt of a cycle item.

    Retries produce a new row instead of overwriting the previous result, so the
    first failure, every retry and the final conclusion stay inspectable.
    """

    __tablename__ = "test_cycle_attempts"
    __table_args__ = (
        UniqueConstraint("cycle_item_id", "attempt_no", name="uq_cycle_attempt_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("test_cycles.id", ondelete="CASCADE"), index=True)
    cycle_item_id: Mapped[int] = mapped_column(
        ForeignKey("test_cycle_items.id", ondelete="CASCADE"), index=True
    )
    asset_type: Mapped[str] = mapped_column(String(32), default="functional")
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_type: Mapped[str] = mapped_column(String(32), default="manual")
    original_run_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    original_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="running")
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    cycle = relationship("TestCycle", back_populates="attempts")
    cycle_item = relationship("TestCycleItem", back_populates="attempts")


class TestCycleReport(Base, TimestampMixin):
    """Immutable snapshot of one batch's report.

    Regenerating keeps the previous revisions so a historical report never
    changes when cases or results are edited afterwards.
    """

    __tablename__ = "test_cycle_reports"
    __table_args__ = (
        UniqueConstraint("cycle_id", "revision", name="uq_cycle_report_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("test_cycles.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    generated_at: Mapped[datetime] = mapped_column(BeijingDateTime, nullable=False)
    generated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    summary_text: Mapped[str] = mapped_column(Text, default="")
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_config: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    cycle = relationship("TestCycle", back_populates="reports")


class ExecutionAttachment(Base, TimestampMixin):
    __tablename__ = "execution_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_item_id: Mapped[int] = mapped_column(
        ForeignKey("test_cycle_items.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    cycle_item = relationship("TestCycleItem", back_populates="attachments")
