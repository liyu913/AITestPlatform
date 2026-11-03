"""test_cases.test_data

Revision ID: b5c6d7e8f9g0
Revises: a4b5c6d7e8f9
Create Date: 2026-10-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9g0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("test_data", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_cases", "test_data")
