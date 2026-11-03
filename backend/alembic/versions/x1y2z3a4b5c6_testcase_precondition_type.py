"""test_cases.precondition and case_type

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-10-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "w0x1y2z3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("precondition", sa.Text(), nullable=True))
    op.add_column("test_cases", sa.Column("case_type", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("test_cases", "case_type")
    op.drop_column("test_cases", "precondition")
