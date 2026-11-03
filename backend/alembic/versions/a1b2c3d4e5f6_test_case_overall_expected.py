"""test_cases.overall_expected

Revision ID: a1b2c3d4e5f6
Revises: 46b9a2c0aaff
Create Date: 2026-05-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "46b9a2c0aaff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL does not allow DEFAULT on TEXT columns; add nullable, backfill, then NOT NULL.
    op.add_column(
        "test_cases",
        sa.Column("overall_expected", sa.Text(), nullable=True),
    )
    op.execute(sa.text("UPDATE test_cases SET overall_expected = '' WHERE overall_expected IS NULL"))
    op.alter_column(
        "test_cases",
        "overall_expected",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("test_cases", "overall_expected")
