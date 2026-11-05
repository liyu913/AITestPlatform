"""Defect classification, fix build, retest and regression gating (B5)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("category", sa.String(32), nullable=False, server_default="product"),
        sa.Column("failure_source_type", sa.String(32), nullable=True),
        sa.Column("failure_source_id", sa.Integer(), nullable=True),
        # MySQL cannot default TEXT: add nullable, backfill, then enforce NOT NULL.
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("actual_result", sa.Text(), nullable=True),
        sa.Column("environment", sa.String(256), nullable=True),
        sa.Column("found_build", sa.String(128), nullable=True),
        sa.Column("fix_build", sa.String(128), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "verified_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("verification_result", sa.String(16), nullable=True),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("regression_cycle_ids_json", sa.JSON(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "closed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    ):
        with op.batch_alter_table("defects") as table:
            table.add_column(column)
    op.create_index("ix_defects_category", "defects", ["category"])
    op.create_index("ix_defects_failure_source_type", "defects", ["failure_source_type"])

    bind = op.get_bind()
    for text_column in ("expected_result", "actual_result", "verification_note"):
        bind.execute(
            sa.text(f"UPDATE defects SET {text_column} = '' WHERE {text_column} IS NULL")
        )
        with op.batch_alter_table("defects") as table:
            table.alter_column(text_column, existing_type=sa.Text(), nullable=False)

    # Existing rows all come from manual cycle items; keep only one defect per
    # (project, item) so the new uniqueness constraint can be created.
    rows = bind.execute(
        sa.text(
            "SELECT id, project_id, cycle_item_id FROM defects "
            "WHERE cycle_item_id IS NOT NULL ORDER BY id"
        )
    ).mappings().all()
    seen: set[tuple[int, int]] = set()
    for row in rows:
        key = (int(row["project_id"]), int(row["cycle_item_id"]))
        if key in seen:
            continue
        seen.add(key)
        bind.execute(
            sa.text(
                "UPDATE defects SET failure_source_type = 'cycle_item', "
                "failure_source_id = :item_id WHERE id = :defect_id"
            ),
            {"item_id": key[1], "defect_id": int(row["id"])},
        )

    with op.batch_alter_table("defects") as table:
        table.create_unique_constraint(
            "uq_defect_failure_source",
            ["project_id", "failure_source_type", "failure_source_id"],
        )

    op.create_table(
        "defect_status_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("defect_id", sa.Integer(), sa.ForeignKey("defects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(64), nullable=False, server_default=""),
        sa.Column("to_status", sa.String(64), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_defect_status_history_defect_id", "defect_status_history", ["defect_id"])
    op.create_index("ix_defect_status_history_project_id", "defect_status_history", ["project_id"])

    bind.execute(
        sa.text(
            "INSERT INTO defect_status_history "
            "(defect_id, project_id, from_status, to_status, comment, created_at, updated_at) "
            "SELECT id, project_id, '', status, '迁移基准', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM defects"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_defect_status_history_project_id", table_name="defect_status_history")
    op.drop_index("ix_defect_status_history_defect_id", table_name="defect_status_history")
    op.drop_table("defect_status_history")

    with op.batch_alter_table("defects") as table:
        table.drop_constraint("uq_defect_failure_source", type_="unique")
    op.drop_index("ix_defects_failure_source_type", table_name="defects")
    op.drop_index("ix_defects_category", table_name="defects")

    for name in (
        "closed_by_id",
        "closed_at",
        "regression_cycle_ids_json",
        "verification_note",
        "verification_result",
        "verified_by_id",
        "verified_at",
        "fix_build",
        "found_build",
        "environment",
        "actual_result",
        "expected_result",
        "failure_source_id",
        "failure_source_type",
        "category",
    ):
        with op.batch_alter_table("defects") as table:
            table.drop_column(name)
