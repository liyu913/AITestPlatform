"""test_cases.module for AI-written functional cases

Revision ID: u8v9w0x1y2z3
Revises: u8v9w0x1y2z
Create Date: 2026-10-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u8v9w0x1y2z3"
down_revision: Union[str, Sequence[str], None] = "u8v9w0x1y2z"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("module", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("test_cases", "module")
