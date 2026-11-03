"""ai_generation_sessions

Revision ID: b3c4d5e6f7a8
Revises: f8e9d0c1b2a3
Create Date: 2026-06-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "f8e9d0c1b2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_generation_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("messages_json", sa.JSON(), nullable=False),
        sa.Column("drafts_json", sa.JSON(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=True),
        sa.Column("knowledge_library_id", sa.Integer(), nullable=True),
        sa.Column("llm_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["knowledge_library_id"], ["knowledge_libraries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_generation_sessions_project_id"),
        "ai_generation_sessions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_generation_sessions_user_id"),
        "ai_generation_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_generation_sessions_project_user_updated",
        "ai_generation_sessions",
        ["project_id", "user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_generation_sessions_project_user_updated", table_name="ai_generation_sessions")
    op.drop_index(op.f("ix_ai_generation_sessions_user_id"), table_name="ai_generation_sessions")
    op.drop_index(op.f("ix_ai_generation_sessions_project_id"), table_name="ai_generation_sessions")
    op.drop_table("ai_generation_sessions")
