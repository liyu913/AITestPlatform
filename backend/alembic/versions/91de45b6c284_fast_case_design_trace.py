"""Preserve AI design evidence and the identity of confirmed drafts."""

from alembic import op
import sqlalchemy as sa

revision = "91de45b6c284"
down_revision = "r6s7t8u9v0w1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("test_cases") as table:
        table.add_column(sa.Column("ai_draft_id", sa.String(36), nullable=True))
        table.add_column(sa.Column("design_trace", sa.JSON(), nullable=True))
        table.create_unique_constraint("uq_test_cases_project_ai_draft", ["project_id", "ai_draft_id"])


def downgrade():
    with op.batch_alter_table("test_cases") as table:
        table.drop_constraint("uq_test_cases_project_ai_draft", type_="unique")
        table.drop_column("design_trace")
        table.drop_column("ai_draft_id")
