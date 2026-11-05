"""R5: report narrative snapshot.

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
"""

from alembic import op
import sqlalchemy as sa

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("test_cycle_reports") as table:
        table.add_column(sa.Column("narrative", sa.Text(), nullable=True))
    with op.batch_alter_table("test_cycle_reports") as table:
        table.add_column(sa.Column("narrative_config", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("test_cycle_reports") as table:
        table.drop_column("narrative_config")
    with op.batch_alter_table("test_cycle_reports") as table:
        table.drop_column("narrative")
