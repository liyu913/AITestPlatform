"""Batch report snapshots (B6)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6g7h8i9j0k1"
down_revision = "e5f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_cycle_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("test_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "generated_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("cycle_id", "revision", name="uq_cycle_report_revision"),
    )
    op.create_index("ix_test_cycle_reports_project_id", "test_cycle_reports", ["project_id"])
    op.create_index("ix_test_cycle_reports_cycle_id", "test_cycle_reports", ["cycle_id"])


def downgrade() -> None:
    op.drop_index("ix_test_cycle_reports_cycle_id", table_name="test_cycle_reports")
    op.drop_index("ix_test_cycle_reports_project_id", table_name="test_cycle_reports")
    op.drop_table("test_cycle_reports")
