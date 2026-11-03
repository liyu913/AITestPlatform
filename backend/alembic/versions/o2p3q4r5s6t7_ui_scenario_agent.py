"""ui scenario agent sessions

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o2p3q4r5s6t7"
down_revision: Union[str, Sequence[str], None] = "n1o2p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ui_scenario_agent_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("testcase_id", sa.Integer(), nullable=True),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("browser", sa.String(length=32), nullable=False, server_default="chromium"),
        sa.Column("messages_json", sa.JSON(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("trace_json", sa.JSON(), nullable=True),
        sa.Column("result_steps_json", sa.JSON(), nullable=True),
        sa.Column("playwright_code", sa.Text(), nullable=False),
        sa.Column("credentials_json", sa.JSON(), nullable=True),
        sa.Column("events_json", sa.JSON(), nullable=True),
        sa.Column("dry_run_status", sa.String(length=32), nullable=True),
        sa.Column("dry_run_error", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("saved_ui_case_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["testcase_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["env_id"], ["ui_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["saved_ui_case_id"], ["ui_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["ui_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index("ix_ui_scenario_agent_sessions_project_id", "ui_scenario_agent_sessions", ["project_id"])
    op.create_index("ix_ui_scenario_agent_sessions_user_id", "ui_scenario_agent_sessions", ["user_id"])
    op.create_index("ix_ui_scenario_agent_sessions_thread_id", "ui_scenario_agent_sessions", ["thread_id"])
    op.create_index("ix_ui_scenario_agent_sessions_status", "ui_scenario_agent_sessions", ["status"])
    op.create_index("ix_ui_scenario_agent_sessions_testcase_id", "ui_scenario_agent_sessions", ["testcase_id"])
    op.create_index("ix_ui_scenario_agent_sessions_env_id", "ui_scenario_agent_sessions", ["env_id"])


def downgrade() -> None:
    op.drop_index("ix_ui_scenario_agent_sessions_env_id", table_name="ui_scenario_agent_sessions")
    op.drop_index("ix_ui_scenario_agent_sessions_testcase_id", table_name="ui_scenario_agent_sessions")
    op.drop_index("ix_ui_scenario_agent_sessions_status", table_name="ui_scenario_agent_sessions")
    op.drop_index("ix_ui_scenario_agent_sessions_thread_id", table_name="ui_scenario_agent_sessions")
    op.drop_index("ix_ui_scenario_agent_sessions_user_id", table_name="ui_scenario_agent_sessions")
    op.drop_index("ix_ui_scenario_agent_sessions_project_id", table_name="ui_scenario_agent_sessions")
    op.drop_table("ui_scenario_agent_sessions")
