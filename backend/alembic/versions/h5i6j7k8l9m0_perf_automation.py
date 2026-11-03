"""perf automation tables

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h5i6j7k8l9m0"
down_revision: Union[str, Sequence[str], None] = "g4h5i6j7k8l9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "perf_scenarios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("case_items_json", sa.JSON(), nullable=True),
        sa.Column("users", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("spawn_rate", sa.Float(), nullable=False, server_default="2"),
        sa.Column("run_time_sec", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("think_time_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sla_json", sa.JSON(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["env_id"], ["api_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_perf_scenarios_project_id"), "perf_scenarios", ["project_id"], unique=False)
    op.create_index(op.f("ix_perf_scenarios_env_id"), "perf_scenarios", ["env_id"], unique=False)

    op.create_table(
        "perf_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("config_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.String(length=64), nullable=True),
        sa.Column("finished_at", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["env_id"], ["api_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scenario_id"], ["perf_scenarios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_perf_runs_project_id"), "perf_runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_perf_runs_scenario_id"), "perf_runs", ["scenario_id"], unique=False)
    op.create_index(op.f("ix_perf_runs_env_id"), "perf_runs", ["env_id"], unique=False)
    op.create_index(op.f("ix_perf_runs_status"), "perf_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_perf_runs_status"), table_name="perf_runs")
    op.drop_index(op.f("ix_perf_runs_env_id"), table_name="perf_runs")
    op.drop_index(op.f("ix_perf_runs_scenario_id"), table_name="perf_runs")
    op.drop_index(op.f("ix_perf_runs_project_id"), table_name="perf_runs")
    op.drop_table("perf_runs")
    op.drop_index(op.f("ix_perf_scenarios_env_id"), table_name="perf_scenarios")
    op.drop_index(op.f("ix_perf_scenarios_project_id"), table_name="perf_scenarios")
    op.drop_table("perf_scenarios")
