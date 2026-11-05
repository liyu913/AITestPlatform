from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.datetime_json import BeijingDateTime
from app.models.base import Base, TimestampMixin


class TestCaseRun(Base, TimestampMixin):
    """Append-only functional case execution record (agent_once / future manual)."""

    __tablename__ = "test_case_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="agent_once")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("ui_scenario_agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Batch item this run belongs to (B4); mirrored into a TestCycleAttempt.
    cycle_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cycle_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    actual_result: Mapped[str] = mapped_column(Text, default="")
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(BeijingDateTime, nullable=True)
