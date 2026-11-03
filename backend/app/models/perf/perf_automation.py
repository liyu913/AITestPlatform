from sqlalchemy import Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PerfScenario(Base, TimestampMixin):
    __tablename__ = "perf_scenarios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    case_items_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    users: Mapped[int] = mapped_column(Integer, default=10)
    spawn_rate: Mapped[float] = mapped_column(Float, default=2.0)
    run_time_sec: Mapped[int] = mapped_column(Integer, default=60)
    think_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    sla_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PerfRun(Base, TimestampMixin):
    __tablename__ = "perf_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("perf_scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    env_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    config_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
