"""ui ai explore jobs

Revision ID: m0n1o2p3q4r5
Revises: l9m0n1o2p3q4
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m0n1o2p3q4r5"
down_revision: Union[str, Sequence[str], None] = "l9m0n1o2p3q4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ui_ai_explore_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("testcase_id", sa.Integer(), nullable=False),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("account_name", sa.String(length=128), nullable=True),
        sa.Column("browser", sa.String(length=32), nullable=False, server_default="chromium"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("trace_json", sa.JSON(), nullable=True),
        sa.Column("result_steps_json", sa.JSON(), nullable=True),
        sa.Column("playwright_code", sa.Text(), nullable=False),
        sa.Column("dry_run_status", sa.String(length=32), nullable=True),
        sa.Column("dry_run_error", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("saved_ui_case_id", sa.Integer(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["testcase_id"], ["test_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["env_id"], ["ui_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["saved_ui_case_id"], ["ui_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ui_ai_explore_jobs_project_id", "ui_ai_explore_jobs", ["project_id"])
    op.create_index("ix_ui_ai_explore_jobs_testcase_id", "ui_ai_explore_jobs", ["testcase_id"])
    op.create_index("ix_ui_ai_explore_jobs_env_id", "ui_ai_explore_jobs", ["env_id"])
    op.create_index("ix_ui_ai_explore_jobs_status", "ui_ai_explore_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ui_ai_explore_jobs_status", table_name="ui_ai_explore_jobs")
    op.drop_index("ix_ui_ai_explore_jobs_env_id", table_name="ui_ai_explore_jobs")
    op.drop_index("ix_ui_ai_explore_jobs_testcase_id", table_name="ui_ai_explore_jobs")
    op.drop_index("ix_ui_ai_explore_jobs_project_id", table_name="ui_ai_explore_jobs")
    op.drop_table("ui_ai_explore_jobs")
