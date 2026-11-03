"""api definitions (interface management)

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i6j7k8l9m0n1"
down_revision: Union[str, Sequence[str], None] = "h5i6j7k8l9m0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("module", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=False, server_default="GET"),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("query_json", sa.JSON(), nullable=True),
        sa.Column("headers_json", sa.JSON(), nullable=True),
        sa.Column("body_type", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("operation_id", sa.String(length=255), nullable=True),
        sa.Column("spec_id", sa.Integer(), nullable=True),
        sa.Column("spec_endpoint_id", sa.Integer(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spec_id"], ["api_specs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["spec_endpoint_id"], ["api_spec_endpoints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_definitions_project_id", "api_definitions", ["project_id"])
    op.create_index("ix_api_definitions_spec_id", "api_definitions", ["spec_id"])
    op.create_index("ix_api_definitions_spec_endpoint_id", "api_definitions", ["spec_endpoint_id"])

    op.add_column("api_cases", sa.Column("api_id", sa.Integer(), nullable=True))
    op.create_index("ix_api_cases_api_id", "api_cases", ["api_id"])
    op.create_foreign_key(
        "fk_api_cases_api_id_api_definitions",
        "api_cases",
        "api_definitions",
        ["api_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_api_cases_api_id_api_definitions", "api_cases", type_="foreignkey")
    op.drop_index("ix_api_cases_api_id", table_name="api_cases")
    op.drop_column("api_cases", "api_id")
    op.drop_index("ix_api_definitions_spec_endpoint_id", table_name="api_definitions")
    op.drop_index("ix_api_definitions_spec_id", table_name="api_definitions")
    op.drop_index("ix_api_definitions_project_id", table_name="api_definitions")
    op.drop_table("api_definitions")
