"""R4: defect analysis records and submission attempts.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
"""

from alembic import op
import sqlalchemy as sa

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "defect_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "defect_id",
            sa.Integer(),
            sa.ForeignKey("defects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("observed", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("evidence_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "confirmed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_defect_analyses_project_id", "defect_analyses", ["project_id"])
    op.create_index("ix_defect_analyses_defect_id", "defect_analyses", ["defect_id"])
    op.create_index("ix_defect_analyses_category", "defect_analyses", ["category"])
    op.create_index("ix_defect_analyses_status", "defect_analyses", ["status"])
    op.create_index("ix_defect_analyses_source_type", "defect_analyses", ["source_type"])

    op.create_table(
        "defect_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "defect_id",
            sa.Integer(),
            sa.ForeignKey("defects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target", sa.String(32), nullable=False, server_default="internal"),
        sa.Column("connector", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
        sa.Column("external_id", sa.String(256), nullable=True),
        sa.Column("external_url", sa.String(512), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "submitted_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("project_id", "defect_id", "request_id", name="uq_defect_submission_req"),
    )
    op.create_index("ix_defect_submissions_project_id", "defect_submissions", ["project_id"])
    op.create_index("ix_defect_submissions_defect_id", "defect_submissions", ["defect_id"])
    op.create_index("ix_defect_submissions_status", "defect_submissions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_defect_submissions_status", table_name="defect_submissions")
    op.drop_index("ix_defect_submissions_defect_id", table_name="defect_submissions")
    op.drop_index("ix_defect_submissions_project_id", table_name="defect_submissions")
    op.drop_table("defect_submissions")

    op.drop_index("ix_defect_analyses_source_type", table_name="defect_analyses")
    op.drop_index("ix_defect_analyses_status", table_name="defect_analyses")
    op.drop_index("ix_defect_analyses_category", table_name="defect_analyses")
    op.drop_index("ix_defect_analyses_defect_id", table_name="defect_analyses")
    op.drop_index("ix_defect_analyses_project_id", table_name="defect_analyses")
    op.drop_table("defect_analyses")
