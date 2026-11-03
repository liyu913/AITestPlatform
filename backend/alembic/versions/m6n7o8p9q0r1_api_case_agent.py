"""api case agent sessions

Revision ID: m6n7o8p9q0r1
Revises: l5m6n7o8p9q0
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m6n7o8p9q0r1"
down_revision: Union[str, Sequence[str], None] = "l5m6n7o8p9q0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def upgrade() -> None:
    if "api_case_agent_sessions" in _tables():
        return
    op.create_table(
        "api_case_agent_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("candidate_definition_ids_json", sa.JSON(), nullable=True),
        sa.Column("messages_json", sa.JSON(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("events_json", sa.JSON(), nullable=True),
        sa.Column("created_case_ids_json", sa.JSON(), nullable=True),
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
        sa.ForeignKeyConstraint(["run_id"], ["api_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_api_case_agent_sessions_project_id",
        "api_case_agent_sessions",
        ["project_id"],
    )
    op.create_index(
        "ix_api_case_agent_sessions_thread_id",
        "api_case_agent_sessions",
        ["thread_id"],
        unique=True,
    )


def downgrade() -> None:
    if "api_case_agent_sessions" not in _tables():
        return
    op.drop_index("ix_api_case_agent_sessions_thread_id", table_name="api_case_agent_sessions")
    op.drop_index("ix_api_case_agent_sessions_project_id", table_name="api_case_agent_sessions")
    op.drop_table("api_case_agent_sessions")
