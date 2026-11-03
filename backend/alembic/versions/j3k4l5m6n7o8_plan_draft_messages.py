"""requirement plan draft chat messages

Revision ID: j3k4l5m6n7o8
Revises: i2j3k4l5m6n7
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j3k4l5m6n7o8"
down_revision: Union[str, Sequence[str], None] = "i2j3k4l5m6n7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("plan_draft_messages_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requirements", "plan_draft_messages_json")
