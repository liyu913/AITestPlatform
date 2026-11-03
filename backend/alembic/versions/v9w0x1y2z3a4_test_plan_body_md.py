"""test plan body as markdown

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v9w0x1y2z3a4"
down_revision: Union[str, Sequence[str], None] = "u8v9w0x1y2z3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("requirements", "plan_draft_json")
    op.add_column("requirements", sa.Column("plan_draft_md", sa.Text(), nullable=True))
    op.drop_column("test_plans", "content_json")
    op.add_column("test_plans", sa.Column("body_md", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_plans", "body_md")
    op.add_column("test_plans", sa.Column("content_json", sa.JSON(), nullable=True))
    op.drop_column("requirements", "plan_draft_md")
    op.add_column("requirements", sa.Column("plan_draft_json", sa.JSON(), nullable=True))
