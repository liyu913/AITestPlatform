"""ui_environments inject_headers / inject_local_storage KV

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-10-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q5r6s7t8u9v0"
down_revision: Union[str, Sequence[str], None] = "p4q5r6s7t8u9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ui_environments", sa.Column("inject_headers_json", sa.JSON(), nullable=True))
    op.add_column("ui_environments", sa.Column("inject_local_storage_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ui_environments", "inject_local_storage_json")
    op.drop_column("ui_environments", "inject_headers_json")
