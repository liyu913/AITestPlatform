"""knowledge library reindex_required

Revision ID: s6t7u8v9w0k1
Revises: q4r5s6t7u8v9
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s6t7u8v9w0k1"
down_revision: Union[str, Sequence[str], None] = "q4r5s6t7u8v9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_libraries",
        sa.Column("reindex_required", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("knowledge_libraries", "reindex_required")
