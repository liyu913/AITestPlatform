"""smaller knowledge library chunk defaults (400/80)

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i2j3k4l5m6n7"
down_revision: Union[str, Sequence[str], None] = "h1i2j3k4l5m6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "knowledge_libraries",
        "chunk_window",
        existing_type=sa.Integer(),
        server_default="400",
        existing_nullable=False,
    )
    op.alter_column(
        "knowledge_libraries",
        "chunk_overlap",
        existing_type=sa.Integer(),
        server_default="80",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "knowledge_libraries",
        "chunk_window",
        existing_type=sa.Integer(),
        server_default="800",
        existing_nullable=False,
    )
    op.alter_column(
        "knowledge_libraries",
        "chunk_overlap",
        existing_type=sa.Integer(),
        server_default="120",
        existing_nullable=False,
    )
