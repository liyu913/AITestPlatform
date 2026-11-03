"""ui_environments auth storageState and token inject

Revision ID: c6d7e8f9g0h1
Revises: b5c6d7e8f9g0
Create Date: 2026-10-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9g0h1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9g0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ui_environments", sa.Column("auth_states_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "ui_environments",
        sa.Column("token_inject", sa.String(length=32), nullable=False, server_default="none"),
    )
    op.add_column("ui_environments", sa.Column("token_storage_key", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("ui_environments", "token_storage_key")
    op.drop_column("ui_environments", "token_inject")
    op.drop_column("ui_environments", "auth_states_encrypted")
