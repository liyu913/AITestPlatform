"""api automation tables

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_environments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("variables_json", sa.JSON(), nullable=True),
        sa.Column("secrets_encrypted", sa.Text(), nullable=True),
        sa.Column("headers_json", sa.JSON(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_environments_project_id"), "api_environments", ["project_id"], unique=False)

    op.create_table(
        "api_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=False, server_default="GET"),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("query_json", sa.JSON(), nullable=True),
        sa.Column("headers_json", sa.JSON(), nullable=True),
        sa.Column("body_type", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("extractors_json", sa.JSON(), nullable=True),
        sa.Column("assertions_json", sa.JSON(), nullable=True),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="15000"),
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
    op.create_index(op.f("ix_api_cases_project_id"), "api_cases", ["project_id"], unique=False)
    op.create_index(op.f("ix_api_cases_manual_case_id"), "api_cases", ["manual_case_id"], unique=False)

    op.create_table(
        "api_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="case"),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.String(length=64), nullable=True),
        sa.Column("finished_at", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["env_id"], ["api_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_runs_project_id"), "api_runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_api_runs_env_id"), "api_runs", ["env_id"], unique=False)
    op.create_index(op.f("ix_api_runs_status"), "api_runs", ["status"], unique=False)

    op.create_table(
        "api_run_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("case_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("request_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_headers_json", sa.JSON(), nullable=True),
        sa.Column("response_body_text", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("assertion_results_json", sa.JSON(), nullable=True),
        sa.Column("extract_results_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["api_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_run_steps_run_id"), "api_run_steps", ["run_id"], unique=False)
    op.create_index(op.f("ix_api_run_steps_project_id"), "api_run_steps", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_run_steps_project_id"), table_name="api_run_steps")
    op.drop_index(op.f("ix_api_run_steps_run_id"), table_name="api_run_steps")
    op.drop_table("api_run_steps")
    op.drop_index(op.f("ix_api_runs_status"), table_name="api_runs")
    op.drop_index(op.f("ix_api_runs_env_id"), table_name="api_runs")
    op.drop_index(op.f("ix_api_runs_project_id"), table_name="api_runs")
    op.drop_table("api_runs")
    op.drop_index(op.f("ix_api_cases_manual_case_id"), table_name="api_cases")
    op.drop_index(op.f("ix_api_cases_project_id"), table_name="api_cases")
    op.drop_table("api_cases")
    op.drop_index(op.f("ix_api_environments_project_id"), table_name="api_environments")
    op.drop_table("api_environments")
