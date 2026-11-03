"""ai session agent fields

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_generation_sessions",
        sa.Column("generate_mode", sa.String(length=16), server_default="fast", nullable=False),
    )
    op.add_column(
        "ai_generation_sessions",
        sa.Column("artifacts_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_generation_sessions", "artifacts_json")
    op.drop_column("ai_generation_sessions", "generate_mode")
