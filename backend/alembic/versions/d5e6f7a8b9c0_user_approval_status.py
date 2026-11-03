"""user approval_status

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("approval_status", sa.String(length=16), server_default="approved", nullable=False),
    )
    op.create_index(op.f("ix_users_approval_status"), "users", ["approval_status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_approval_status"), table_name="users")
    op.drop_column("users", "approval_status")
