"""ui_cases: migrate auth_mode none → NULL (follow-env)

Old semantics treated case auth_mode=none as follow-env inject.
New semantics: none = durable opt-out (login); NULL = follow env.

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-10-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r6s7t8u9v0w1"
down_revision: Union[str, Sequence[str], None] = "q5r6s7t8u9v0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE ui_cases SET auth_mode = NULL WHERE auth_mode = 'none'")
    )


def downgrade() -> None:
    # Irreversible data meaning change; leave NULL rows as-is.
    pass
