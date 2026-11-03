"""ui_cases.script

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-10-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y2z3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "x1y2z3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL rejects DEFAULT on TEXT/BLOB (error 1101). Add nullable, backfill, then require a value.
    op.add_column("ui_cases", sa.Column("script", sa.Text(), nullable=True))
    op.execute("UPDATE ui_cases SET script = '' WHERE script IS NULL")
    op.alter_column("ui_cases", "script", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    op.drop_column("ui_cases", "script")
