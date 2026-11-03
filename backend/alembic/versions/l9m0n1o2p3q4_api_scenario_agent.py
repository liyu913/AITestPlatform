"""api suite steps_json + scenario agent sessions

Revision ID: l9m0n1o2p3q4
Revises: k8l9m0n1o2p3
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l9m0n1o2p3q4"
down_revision: Union[str, Sequence[str], None] = "k8l9m0n1o2p3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)}


def _tables() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def upgrade() -> None:
    if "steps_json" not in _cols("api_suites"):
        op.add_column("api_suites", sa.Column("steps_json", sa.JSON(), nullable=True))
    if "step_key" not in _cols("api_run_steps"):
        op.add_column("api_run_steps", sa.Column("step_key", sa.String(length=64), nullable=True))
    if "api_scenario_agent_sessions" not in _tables():
        op.create_table(
            "api_scenario_agent_sessions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("candidate_case_ids_json", sa.JSON(), nullable=True),
            sa.Column("plan_json", sa.JSON(), nullable=True),
            sa.Column("events_json", sa.JSON(), nullable=True),
            sa.Column("suite_id", sa.Integer(), nullable=True),
            sa.Column("run_id", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["suite_id"], ["api_suites.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["run_id"], ["api_runs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_api_scenario_agent_sessions_project_id",
            "api_scenario_agent_sessions",
            ["project_id"],
        )
        op.create_index(
            "ix_api_scenario_agent_sessions_thread_id",
            "api_scenario_agent_sessions",
            ["thread_id"],
            unique=True,
        )


def downgrade() -> None:
    if "api_scenario_agent_sessions" in _tables():
        op.drop_index(
            "ix_api_scenario_agent_sessions_thread_id",
            table_name="api_scenario_agent_sessions",
        )
        op.drop_index(
            "ix_api_scenario_agent_sessions_project_id",
            table_name="api_scenario_agent_sessions",
        )
        op.drop_table("api_scenario_agent_sessions")
    if "step_key" in _cols("api_run_steps"):
        op.drop_column("api_run_steps", "step_key")
    if "steps_json" in _cols("api_suites"):
        op.drop_column("api_suites", "steps_json")
