"""Enhance api runner: scripts + stronger assertions; add specs & schedules

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_cases", sa.Column("pre_script", sa.Text(), nullable=True))
    op.add_column("api_cases", sa.Column("post_script", sa.Text(), nullable=True))

    op.create_table(
        "api_specs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="openapi"),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_specs_project_id"), "api_specs", ["project_id"], unique=False)

    op.create_table(
        "api_spec_endpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("spec_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("operation_id", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.String(length=512), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("request_sample_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spec_id"], ["api_specs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_spec_endpoints_spec_id"), "api_spec_endpoints", ["spec_id"], unique=False)
    op.create_index(op.f("ix_api_spec_endpoints_project_id"), "api_spec_endpoints", ["project_id"], unique=False)

    op.create_table(
        "api_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=False),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("cron_expr", sa.String(length=128), nullable=False, server_default="0 * * * *"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_run_at", sa.String(length=64), nullable=True),
        sa.Column("next_run_at", sa.String(length=64), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["env_id"], ["api_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_id"], ["api_suites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_schedules_project_id"), "api_schedules", ["project_id"], unique=False)
    op.create_index(op.f("ix_api_schedules_suite_id"), "api_schedules", ["suite_id"], unique=False)
    op.create_index(op.f("ix_api_schedules_enabled"), "api_schedules", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_schedules_enabled"), table_name="api_schedules")
    op.drop_index(op.f("ix_api_schedules_suite_id"), table_name="api_schedules")
    op.drop_index(op.f("ix_api_schedules_project_id"), table_name="api_schedules")
    op.drop_table("api_schedules")
    op.drop_index(op.f("ix_api_spec_endpoints_project_id"), table_name="api_spec_endpoints")
    op.drop_index(op.f("ix_api_spec_endpoints_spec_id"), table_name="api_spec_endpoints")
    op.drop_table("api_spec_endpoints")
    op.drop_index(op.f("ix_api_specs_project_id"), table_name="api_specs")
    op.drop_table("api_specs")
    op.drop_column("api_cases", "post_script")
    op.drop_column("api_cases", "pre_script")
