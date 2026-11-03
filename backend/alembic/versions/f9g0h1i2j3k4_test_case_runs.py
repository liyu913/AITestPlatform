"""test_case_runs for functional agent_once execution history

Revision ID: f9g0h1i2j3k4
Revises: e8f9g0h1i2j3
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9g0h1i2j3k4"
down_revision: Union[str, Sequence[str], None] = "e8f9g0h1i2j3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_case_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="agent_once"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("actual_result", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["env_id"], ["ui_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ui_scenario_agent_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_test_case_runs_project_id", "test_case_runs", ["project_id"])
    op.create_index("ix_test_case_runs_case_id", "test_case_runs", ["case_id"])
    op.create_index("ix_test_case_runs_status", "test_case_runs", ["status"])
    op.create_index("ix_test_case_runs_env_id", "test_case_runs", ["env_id"])
    op.create_index("ix_test_case_runs_session_id", "test_case_runs", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_test_case_runs_session_id", table_name="test_case_runs")
    op.drop_index("ix_test_case_runs_env_id", table_name="test_case_runs")
    op.drop_index("ix_test_case_runs_status", table_name="test_case_runs")
    op.drop_index("ix_test_case_runs_case_id", table_name="test_case_runs")
    op.drop_index("ix_test_case_runs_project_id", table_name="test_case_runs")
    op.drop_table("test_case_runs")
