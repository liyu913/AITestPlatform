"""api_specs.raw_text MEDIUMTEXT for large OpenAPI docs

Revision ID: n1o2p3q4r5s6
Revises: n0o1p2q3r4s5
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, Sequence[str], None] = "n0o1p2q3r4s5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.alter_column(
            "api_specs",
            "raw_text",
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.alter_column(
            "api_specs",
            "raw_text",
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=True,
        )
