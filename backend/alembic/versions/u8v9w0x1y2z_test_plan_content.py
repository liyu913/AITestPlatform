"""test plan content and requirement draft

Revision ID: u8v9w0x1y2z
Revises: t7u8v9w0x1y2
Create Date: 2026-10-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u8v9w0x1y2z"
down_revision: Union[str, Sequence[str], None] = "t7u8v9w0x1y2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("plan_draft_json", sa.JSON(), nullable=True))
    op.add_column("requirements", sa.Column("plan_draft_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("test_plans", sa.Column("requirement_id", sa.Integer(), nullable=True))
    op.add_column("test_plans", sa.Column("content_json", sa.JSON(), nullable=True))
    op.create_index("ix_test_plans_requirement_id", "test_plans", ["requirement_id"])
    op.create_foreign_key(
        "fk_test_plans_requirement_id_requirements",
        "test_plans",
        "requirements",
        ["requirement_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_test_plans_requirement_id_requirements", "test_plans", type_="foreignkey")
    op.drop_index("ix_test_plans_requirement_id", table_name="test_plans")
    op.drop_column("test_plans", "content_json")
    op.drop_column("test_plans", "requirement_id")
    op.drop_column("requirements", "plan_draft_at")
    op.drop_column("requirements", "plan_draft_json")
