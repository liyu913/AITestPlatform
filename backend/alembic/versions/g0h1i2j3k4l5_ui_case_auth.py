"""ui_cases auth_mode and account_name defaults

Revision ID: g0h1i2j3k4l5
Revises: f9g0h1i2j3k4
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g0h1i2j3k4l5"
down_revision: Union[str, Sequence[str], None] = "f9g0h1i2j3k4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ui_cases", sa.Column("auth_mode", sa.String(length=32), nullable=True))
    op.add_column("ui_cases", sa.Column("account_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("ui_cases", "account_name")
    op.drop_column("ui_cases", "auth_mode")
