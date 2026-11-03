"""app automation tables

Revision ID: g4h5i6j7k8l9
Revises: f3a4b5c6d7e8
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g4h5i6j7k8l9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="android"),
        sa.Column("appium_server_url", sa.String(length=1024), nullable=False, server_default="http://127.0.0.1:4723"),
        sa.Column("device_name", sa.String(length=255), nullable=False, server_default="Android Emulator"),
        sa.Column("udid", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("platform_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("automation_name", sa.String(length=64), nullable=False, server_default="UiAutomator2"),
        sa.Column("app_package", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("app_activity", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("bundle_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("app_path", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("no_reset", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("full_reset", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("caps_json", sa.JSON(), nullable=True),
        sa.Column("variables_json", sa.JSON(), nullable=True),
        sa.Column("secrets_encrypted", sa.Text(), nullable=True),
        sa.Column("accounts_json", sa.JSON(), nullable=True),
        sa.Column("queue_name", sa.String(length=64), nullable=False, server_default="app"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("default_timeout_ms", sa.Integer(), nullable=False, server_default="15000"),
        sa.Column("step_retries", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_environments_project_id"), "app_environments", ["project_id"], unique=False)

    op.create_table(
        "app_elements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("page_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False, server_default="id"),
        sa.Column("selector", sa.String(length=1024), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_elements_project_id"), "app_elements", ["project_id"], unique=False)

    op.create_table(
        "app_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("steps_json", sa.JSON(), nullable=True),
        sa.Column("manual_case_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="draft"),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["manual_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_cases_project_id"), "app_cases", ["project_id"], unique=False)
    op.create_index(op.f("ix_app_cases_manual_case_id"), "app_cases", ["manual_case_id"], unique=False)

    op.create_table(
        "app_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="case"),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="android"),
        sa.Column("account_name", sa.String(length=128), nullable=True),
        sa.Column("queue_name", sa.String(length=64), nullable=False, server_default="app"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.String(length=64), nullable=True),
        sa.Column("finished_at", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["env_id"], ["app_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_runs_project_id"), "app_runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_app_runs_env_id"), "app_runs", ["env_id"], unique=False)
    op.create_index(op.f("ix_app_runs_status"), "app_runs", ["status"], unique=False)

    op.create_table(
        "app_run_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("case_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("selector", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("screenshot_key", sa.String(length=1024), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["app_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_run_steps_run_id"), "app_run_steps", ["run_id"], unique=False)
    op.create_index(op.f("ix_app_run_steps_project_id"), "app_run_steps", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_app_run_steps_project_id"), table_name="app_run_steps")
    op.drop_index(op.f("ix_app_run_steps_run_id"), table_name="app_run_steps")
    op.drop_table("app_run_steps")
    op.drop_index(op.f("ix_app_runs_status"), table_name="app_runs")
    op.drop_index(op.f("ix_app_runs_env_id"), table_name="app_runs")
    op.drop_index(op.f("ix_app_runs_project_id"), table_name="app_runs")
    op.drop_table("app_runs")
    op.drop_index(op.f("ix_app_cases_manual_case_id"), table_name="app_cases")
    op.drop_index(op.f("ix_app_cases_project_id"), table_name="app_cases")
    op.drop_table("app_cases")
    op.drop_index(op.f("ix_app_elements_project_id"), table_name="app_elements")
    op.drop_table("app_elements")
    op.drop_index(op.f("ix_app_environments_project_id"), table_name="app_environments")
    op.drop_table("app_environments")
