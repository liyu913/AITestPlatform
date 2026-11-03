"""system_ai_settings

Revision ID: e7f8a9b0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-05-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_ai_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("llm_api_base", sa.String(length=1024), nullable=True),
        sa.Column("llm_model", sa.String(length=256), nullable=True),
        sa.Column("llm_allowed_models", sa.Text(), nullable=True),
        sa.Column("llm_api_key_enc", sa.Text(), nullable=True),
        sa.Column("embedding_api_base", sa.String(length=1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=256), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_api_key_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO system_ai_settings (id) VALUES (1)"
        )
    )


def downgrade() -> None:
    op.drop_table("system_ai_settings")
