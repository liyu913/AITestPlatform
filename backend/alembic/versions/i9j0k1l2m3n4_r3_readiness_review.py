"""R3: execution readiness snapshot and review assignment.

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
"""

from alembic import op
import sqlalchemy as sa

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("test_cases") as table:
        table.add_column(
            sa.Column(
                "assigned_reviewer_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    with op.batch_alter_table("test_cases") as table:
        table.add_column(sa.Column("review_due_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_test_cases_assigned_reviewer_id", "test_cases", ["assigned_reviewer_id"]
    )

    with op.batch_alter_table("test_cycles") as table:
        table.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("test_cycles") as table:
        table.add_column(sa.Column("readiness_json", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("test_cycles") as table:
        table.drop_column("readiness_json")
    with op.batch_alter_table("test_cycles") as table:
        table.drop_column("started_at")

    op.drop_index("ix_test_cases_assigned_reviewer_id", table_name="test_cases")
    with op.batch_alter_table("test_cases") as table:
        table.drop_column("review_due_at")
    with op.batch_alter_table("test_cases") as table:
        table.drop_column("assigned_reviewer_id")
