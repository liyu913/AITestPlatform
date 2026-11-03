"""ui enterprise: suites, schedules, elements, env isolation

Revision ID: e2f3a4b5c6d7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ui_environments", sa.Column("browsers_json", sa.JSON(), nullable=True))
    op.add_column("ui_environments", sa.Column("accounts_json", sa.JSON(), nullable=True))
    op.add_column(
        "ui_environments",
        sa.Column("queue_name", sa.String(length=64), nullable=False, server_default="ui"),
    )
    op.add_column(
        "ui_environments",
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "ui_environments",
        sa.Column("default_timeout_ms", sa.Integer(), nullable=False, server_default="15000"),
    )
    op.add_column(
        "ui_environments",
        sa.Column("step_retries", sa.Integer(), nullable=False, server_default="1"),
    )

    op.add_column(
        "ui_runs",
        sa.Column("browser", sa.String(length=32), nullable=False, server_default="chromium"),
    )
    op.add_column("ui_runs", sa.Column("account_name", sa.String(length=128), nullable=True))
    op.add_column(
        "ui_runs",
        sa.Column("queue_name", sa.String(length=64), nullable=False, server_default="ui"),
    )

    op.add_column("ui_run_steps", sa.Column("case_id", sa.Integer(), nullable=True))
    op.add_column(
        "ui_run_steps",
        sa.Column("case_name", sa.String(length=512), nullable=False, server_default=""),
    )
    op.add_column(
        "ui_run_steps",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "ui_elements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("page_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("selector", sa.String(length=1024), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ui_elements_project_id"), "ui_elements", ["project_id"], unique=False)

    op.create_table(
        "ui_suites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("case_ids_json", sa.JSON(), nullable=True),
        sa.Column("stop_on_fail", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("browsers_json", sa.JSON(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["env_id"], ["ui_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ui_suites_project_id"), "ui_suites", ["project_id"], unique=False)

    op.create_table(
        "ui_schedules",
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
        sa.ForeignKeyConstraint(["env_id"], ["ui_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_id"], ["ui_suites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ui_schedules_project_id"), "ui_schedules", ["project_id"], unique=False)
    op.create_index(op.f("ix_ui_schedules_suite_id"), "ui_schedules", ["suite_id"], unique=False)
    op.create_index(op.f("ix_ui_schedules_enabled"), "ui_schedules", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ui_schedules_enabled"), table_name="ui_schedules")
    op.drop_index(op.f("ix_ui_schedules_suite_id"), table_name="ui_schedules")
    op.drop_index(op.f("ix_ui_schedules_project_id"), table_name="ui_schedules")
    op.drop_table("ui_schedules")
    op.drop_index(op.f("ix_ui_suites_project_id"), table_name="ui_suites")
    op.drop_table("ui_suites")
    op.drop_index(op.f("ix_ui_elements_project_id"), table_name="ui_elements")
    op.drop_table("ui_elements")
    op.drop_column("ui_run_steps", "retry_count")
    op.drop_column("ui_run_steps", "case_name")
    op.drop_column("ui_run_steps", "case_id")
    op.drop_column("ui_runs", "queue_name")
    op.drop_column("ui_runs", "account_name")
    op.drop_column("ui_runs", "browser")
    op.drop_column("ui_environments", "step_retries")
    op.drop_column("ui_environments", "default_timeout_ms")
    op.drop_column("ui_environments", "max_concurrency")
    op.drop_column("ui_environments", "queue_name")
    op.drop_column("ui_environments", "accounts_json")
    op.drop_column("ui_environments", "browsers_json")
