"""knowledge_documents.category

Revision ID: e8f9g0h1i2j3
Revises: d7e8f9g0h1i2
Create Date: 2026-10-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9g0h1i2j3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9g0h1i2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("category", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "category")
