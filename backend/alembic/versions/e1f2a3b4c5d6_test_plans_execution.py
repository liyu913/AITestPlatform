"""test plans execution defects coverage

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("iteration", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="draft"),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_test_plans_project_id"), "test_plans", ["project_id"], unique=False)

    op.create_table(
        "test_cycles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="draft"),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["test_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_test_cycles_plan_id"), "test_cycles", ["plan_id"], unique=False)

    op.create_table(
        "test_cycle_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=64), nullable=False, server_default="untested"),
        sa.Column("actual_result", sa.Text(), nullable=True),
        sa.Column("executed_by_id", sa.Integer(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cycle_id"], ["test_cycles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["executed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "case_id", name="uq_cycle_case"),
    )
    op.create_index(op.f("ix_test_cycle_items_cycle_id"), "test_cycle_items", ["cycle_id"], unique=False)
    op.create_index(op.f("ix_test_cycle_items_case_id"), "test_cycle_items", ["case_id"], unique=False)

    op.create_table(
        "execution_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cycle_item_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("uploader_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["cycle_item_id"], ["test_cycle_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_execution_attachments_cycle_item_id"),
        "execution_attachments",
        ["cycle_item_id"],
        unique=False,
    )

    op.create_table(
        "defects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=64), nullable=False, server_default="P2"),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="open"),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("cycle_item_id", sa.Integer(), nullable=True),
        sa.Column("requirement_id", sa.Integer(), nullable=True),
        sa.Column("reporter_id", sa.Integer(), nullable=True),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("external_system", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cycle_item_id"], ["test_cycle_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_defects_project_id"), "defects", ["project_id"], unique=False)
    op.create_index(op.f("ix_defects_case_id"), "defects", ["case_id"], unique=False)
    op.create_index(op.f("ix_defects_cycle_item_id"), "defects", ["cycle_item_id"], unique=False)
    op.create_index(op.f("ix_defects_requirement_id"), "defects", ["requirement_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_defects_requirement_id"), table_name="defects")
    op.drop_index(op.f("ix_defects_cycle_item_id"), table_name="defects")
    op.drop_index(op.f("ix_defects_case_id"), table_name="defects")
    op.drop_index(op.f("ix_defects_project_id"), table_name="defects")
    op.drop_table("defects")
    op.drop_index(op.f("ix_execution_attachments_cycle_item_id"), table_name="execution_attachments")
    op.drop_table("execution_attachments")
    op.drop_index(op.f("ix_test_cycle_items_case_id"), table_name="test_cycle_items")
    op.drop_index(op.f("ix_test_cycle_items_cycle_id"), table_name="test_cycle_items")
    op.drop_table("test_cycle_items")
    op.drop_index(op.f("ix_test_cycles_plan_id"), table_name="test_cycles")
    op.drop_table("test_cycles")
    op.drop_index(op.f("ix_test_plans_project_id"), table_name="test_plans")
    op.drop_table("test_plans")
