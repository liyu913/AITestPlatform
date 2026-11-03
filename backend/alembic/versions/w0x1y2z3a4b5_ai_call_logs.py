"""ai call logs

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w0x1y2z3a4b5"
down_revision: Union[str, Sequence[str], None] = "v9w0x1y2z3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_call_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("scene", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_text", sa.Text(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_call_logs_created_at"), "ai_call_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_call_logs_kind"), "ai_call_logs", ["kind"], unique=False)
    op.create_index(op.f("ix_ai_call_logs_scene"), "ai_call_logs", ["scene"], unique=False)
    op.create_index(op.f("ix_ai_call_logs_status"), "ai_call_logs", ["status"], unique=False)
    op.create_index(op.f("ix_ai_call_logs_user_id"), "ai_call_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_ai_call_logs_project_id"), "ai_call_logs", ["project_id"], unique=False)
    op.create_index(op.f("ix_ai_call_logs_request_id"), "ai_call_logs", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_call_logs_request_id"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_project_id"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_user_id"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_status"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_scene"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_kind"), table_name="ai_call_logs")
    op.drop_index(op.f("ix_ai_call_logs_created_at"), table_name="ai_call_logs")
    op.drop_table("ai_call_logs")
