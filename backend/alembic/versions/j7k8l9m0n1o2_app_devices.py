"""app device pool

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j7k8l9m0n1o2"
down_revision: Union[str, Sequence[str], None] = "i6j7k8l9m0n1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="android"),
        sa.Column("udid", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("device_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("platform_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("appium_server_url", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("automation_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="offline"),
        sa.Column("worker_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("last_heartbeat_at", sa.String(length=64), nullable=True),
        sa.Column("current_run_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_devices_project_id"), "app_devices", ["project_id"], unique=False)
    op.create_index(op.f("ix_app_devices_status"), "app_devices", ["status"], unique=False)

    op.add_column("app_environments", sa.Column("device_tags_json", sa.JSON(), nullable=True))
    op.add_column(
        "app_environments",
        sa.Column("use_device_pool", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )

    op.add_column("app_runs", sa.Column("device_id", sa.Integer(), nullable=True))
    op.add_column("app_runs", sa.Column("device_tags_json", sa.JSON(), nullable=True))
    op.add_column("app_runs", sa.Column("device_snapshot_json", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_app_runs_device_id_app_devices",
        "app_runs",
        "app_devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_app_runs_device_id"), "app_runs", ["device_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_app_runs_device_id"), table_name="app_runs")
    op.drop_constraint("fk_app_runs_device_id_app_devices", "app_runs", type_="foreignkey")
    op.drop_column("app_runs", "device_snapshot_json")
    op.drop_column("app_runs", "device_tags_json")
    op.drop_column("app_runs", "device_id")
    op.drop_column("app_environments", "use_device_pool")
    op.drop_column("app_environments", "device_tags_json")
    op.drop_index(op.f("ix_app_devices_status"), table_name="app_devices")
    op.drop_index(op.f("ix_app_devices_project_id"), table_name="app_devices")
    op.drop_table("app_devices")
