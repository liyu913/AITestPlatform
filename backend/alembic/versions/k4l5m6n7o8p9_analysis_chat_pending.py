"""requirement analysis chat messages and pending

Revision ID: k4l5m6n7o8p9
Revises: j3k4l5m6n7o8
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k4l5m6n7o8p9"
down_revision: Union[str, Sequence[str], None] = "j3k4l5m6n7o8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("analysis_messages_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "requirements",
        sa.Column("analysis_pending_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requirements", "analysis_pending_json")
    op.drop_column("requirements", "analysis_messages_json")
