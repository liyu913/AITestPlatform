"""requirement source file fields

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("source_filename", sa.String(length=512), nullable=True))
    op.add_column("requirements", sa.Column("source_mime", sa.String(length=255), nullable=True))
    op.add_column("requirements", sa.Column("storage_key", sa.String(length=1024), nullable=True))
    op.add_column("requirements", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("requirements", sa.Column("extracted_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("requirements", "extracted_text")
    op.drop_column("requirements", "size_bytes")
    op.drop_column("requirements", "storage_key")
    op.drop_column("requirements", "source_mime")
    op.drop_column("requirements", "source_filename")
