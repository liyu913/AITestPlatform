"""requirement analysis result

Revision ID: t7u8v9w0x1y2
Revises: r5s6t7u8v9w0, s6t7u8v9w0k1
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t7u8v9w0x1y2"
down_revision: Union[str, Sequence[str], None] = ("r5s6t7u8v9w0", "s6t7u8v9w0k1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("analysis_json", sa.JSON(), nullable=True))
    op.add_column("requirements", sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("requirements", "analyzed_at")
    op.drop_column("requirements", "analysis_json")
