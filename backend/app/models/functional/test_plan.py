from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
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
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    cycles = relationship(
        "TestCycle",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="TestCycle.id",
    )


class TestCycle(Base, TimestampMixin):
    __tablename__ = "test_cycles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(64), default="draft")
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    plan = relationship("TestPlan", back_populates="cycles")
    items = relationship(
        "TestCycleItem",
        back_populates="cycle",
        cascade="all, delete-orphan",
        order_by="TestCycleItem.id",
    )


class TestCycleItem(Base, TimestampMixin):
    __tablename__ = "test_cycle_items"
    __table_args__ = (UniqueConstraint("cycle_id", "case_id", name="uq_cycle_case"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("test_cycles.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    result: Mapped[str] = mapped_column(String(64), default="untested")
    actual_result: Mapped[str] = mapped_column(Text, default="")
    executed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)

    cycle = relationship("TestCycle", back_populates="items")
    attachments = relationship(
        "ExecutionAttachment",
        back_populates="cycle_item",
        cascade="all, delete-orphan",
        order_by="ExecutionAttachment.id",
    )


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
