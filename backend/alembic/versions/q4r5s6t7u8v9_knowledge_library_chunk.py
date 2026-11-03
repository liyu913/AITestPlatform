"""knowledge library chunk settings

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-10-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q4r5s6t7u8v9"
down_revision: Union[str, Sequence[str], None] = "p3q4r5s6t7u8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_libraries",
        sa.Column("chunk_mode", sa.String(length=32), nullable=False, server_default="window"),
    )
    op.add_column(
        "knowledge_libraries",
        sa.Column("chunk_window", sa.Integer(), nullable=False, server_default="800"),
    )
    op.add_column(
        "knowledge_libraries",
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default="120"),
    )


def downgrade() -> None:
    op.drop_column("knowledge_libraries", "chunk_overlap")
    op.drop_column("knowledge_libraries", "chunk_window")
    op.drop_column("knowledge_libraries", "chunk_mode")
