"""Link automation runs to batch items (B4 stage 2)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6g7h8i9"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None

_RUN_TABLES = ("api_runs", "ui_runs", "test_case_runs")


def upgrade() -> None:
    for table_name in _RUN_TABLES:
        with op.batch_alter_table(table_name) as table:
            table.add_column(
                sa.Column(
                    "cycle_item_id",
                    sa.Integer(),
                    sa.ForeignKey("test_cycle_items.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
        op.create_index(f"ix_{table_name}_cycle_item_id", table_name, ["cycle_item_id"])

    # One attempt per (batch item, original run) so duplicated callbacks update
    # the same evidence instead of appending noise.
    with op.batch_alter_table("test_cycle_attempts") as table:
        table.create_unique_constraint(
            "uq_cycle_attempt_run",
            ["cycle_item_id", "original_run_type", "original_run_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("test_cycle_attempts") as table:
        table.drop_constraint("uq_cycle_attempt_run", type_="unique")

    for table_name in _RUN_TABLES:
        op.drop_index(f"ix_{table_name}_cycle_item_id", table_name=table_name)
        with op.batch_alter_table(table_name) as table:
            table.drop_column("cycle_item_id")
