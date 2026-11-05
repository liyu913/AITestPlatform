"""Batch configuration snapshot, typed assets and execution attempts (B4 stage 1)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_build", sa.String(128), nullable=True),
        sa.Column("env_kind", sa.String(16), nullable=True),
        sa.Column("env_id", sa.Integer(), nullable=True),
        sa.Column("env_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("credential_ref", sa.String(128), nullable=True),
        # MySQL cannot default a TEXT column: add nullable, backfill, enforce NOT NULL.
        sa.Column("data_note", sa.Text(), nullable=True),
    ):
        with op.batch_alter_table("test_cycles") as table:
            table.add_column(column)
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE test_cycles SET data_note = '' WHERE data_note IS NULL"))
    with op.batch_alter_table("test_cycles") as table:
        table.alter_column("data_note", existing_type=sa.Text(), nullable=False)
    op.create_index("ix_test_cycles_owner_id", "test_cycles", ["owner_id"])

    for column in (
        sa.Column("asset_type", sa.String(32), nullable=False, server_default="functional"),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("asset_version", sa.Integer(), nullable=True),
        sa.Column("last_run_type", sa.String(32), nullable=True),
        sa.Column("last_run_id", sa.Integer(), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="0"),
    ):
        with op.batch_alter_table("test_cycle_items") as table:
            table.add_column(column)

    # Non-functional assets have no manual case, so case_id must be optional.
    with op.batch_alter_table("test_cycle_items") as table:
        table.alter_column(
            "case_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
    op.create_index("ix_test_cycle_items_asset_type", "test_cycle_items", ["asset_type"])
    with op.batch_alter_table("test_cycle_items") as table:
        table.create_unique_constraint(
            "uq_cycle_asset", ["cycle_id", "asset_type", "asset_id"]
        )

    op.create_table(
        "test_cycle_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("test_cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "cycle_item_id",
            sa.Integer(),
            sa.ForeignKey("test_cycle_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", sa.String(32), nullable=False, server_default="functional"),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("execution_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("original_run_type", sa.String(32), nullable=True),
        sa.Column("original_run_id", sa.Integer(), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("cycle_item_id", "attempt_no", name="uq_cycle_attempt_no"),
    )
    op.create_index("ix_test_cycle_attempts_project_id", "test_cycle_attempts", ["project_id"])
    op.create_index("ix_test_cycle_attempts_cycle_id", "test_cycle_attempts", ["cycle_id"])
    op.create_index("ix_test_cycle_attempts_cycle_item_id", "test_cycle_attempts", ["cycle_item_id"])

    # Existing rows are all manual functional cases.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE test_cycle_items SET asset_type = 'functional', asset_id = case_id "
            "WHERE asset_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_test_cycle_attempts_cycle_item_id", table_name="test_cycle_attempts")
    op.drop_index("ix_test_cycle_attempts_cycle_id", table_name="test_cycle_attempts")
    op.drop_index("ix_test_cycle_attempts_project_id", table_name="test_cycle_attempts")
    op.drop_table("test_cycle_attempts")

    with op.batch_alter_table("test_cycle_items") as table:
        table.drop_constraint("uq_cycle_asset", type_="unique")
    op.drop_index("ix_test_cycle_items_asset_type", table_name="test_cycle_items")

    # Rows that only belonged to an automation asset cannot exist without the
    # column, so clear them before restoring the NOT NULL constraint.
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM test_cycle_items WHERE case_id IS NULL"))
    with op.batch_alter_table("test_cycle_items") as table:
        table.alter_column(
            "case_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
    for name in (
        "attempt_no",
        "last_run_id",
        "last_run_type",
        "asset_version",
        "asset_id",
        "asset_type",
    ):
        with op.batch_alter_table("test_cycle_items") as table:
            table.drop_column(name)

    op.drop_index("ix_test_cycles_owner_id", table_name="test_cycles")
    for name in (
        "data_note",
        "credential_ref",
        "env_snapshot_json",
        "env_id",
        "env_kind",
        "target_build",
        "owner_id",
    ):
        with op.batch_alter_table("test_cycles") as table:
            table.drop_column(name)
