"""R1: test schemes, plan-to-scheme reference and formal case publish pointer.

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
"""

from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "f6g7h8i9j0k1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_schemes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("current_version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("risks", sa.Text(), nullable=False),
        sa.Column("methods", sa.Text(), nullable=False),
        sa.Column("exit_criteria", sa.Text(), nullable=False),
        sa.Column(
            "creator_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_test_schemes_project_id", "test_schemes", ["project_id"])
    op.create_index("ix_test_schemes_status", "test_schemes", ["status"])
    op.create_index("ix_test_schemes_owner_id", "test_schemes", ["owner_id"])

    op.create_table(
        "test_scheme_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "scheme_id",
            sa.Integer(),
            sa.ForeignKey("test_schemes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("change_note", sa.String(512), nullable=True),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("scheme_id", "version_no", name="uq_scheme_version_no"),
    )
    op.create_index("ix_test_scheme_versions_scheme_id", "test_scheme_versions", ["scheme_id"])
    op.create_index("ix_test_scheme_versions_project_id", "test_scheme_versions", ["project_id"])

    with op.batch_alter_table("test_plans") as table:
        table.add_column(
            sa.Column(
                "scheme_id",
                sa.Integer(),
                sa.ForeignKey("test_schemes.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    with op.batch_alter_table("test_plans") as table:
        table.add_column(
            sa.Column(
                "scheme_version_id",
                sa.Integer(),
                sa.ForeignKey("test_scheme_versions.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    op.create_index("ix_test_plans_scheme_id", "test_plans", ["scheme_id"])

    with op.batch_alter_table("test_cases") as table:
        table.add_column(
            sa.Column(
                "published_version_id",
                sa.Integer(),
                sa.ForeignKey("test_case_versions.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    with op.batch_alter_table("test_cases") as table:
        table.add_column(sa.Column("published_version_no", sa.Integer(), nullable=True))
    with op.batch_alter_table("test_cases") as table:
        table.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
    op.create_index("ix_test_cases_published_version_no", "test_cases", ["published_version_no"])

    # Legacy confirmed cases already went through review: publish their latest
    # confirmed version so the formal library is not empty after the upgrade.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id FROM test_cases WHERE lifecycle_status = 'confirmed'"
        )
    ).mappings().all()
    for row in rows:
        case_id = int(row["id"])
        version = bind.execute(
            sa.text(
                "SELECT id, version_no FROM test_case_versions "
                "WHERE case_id = :case_id AND lifecycle_status = 'confirmed' "
                "ORDER BY version_no DESC LIMIT 1"
            ),
            {"case_id": case_id},
        ).mappings().first()
        if version is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE test_cases SET published_version_id = :vid, "
                "published_version_no = :vno, published_at = CURRENT_TIMESTAMP "
                "WHERE id = :case_id"
            ),
            {"vid": int(version["id"]), "vno": int(version["version_no"]), "case_id": case_id},
        )


def downgrade() -> None:
    op.drop_index("ix_test_cases_published_version_no", table_name="test_cases")
    for column in ("published_at", "published_version_no", "published_version_id"):
        with op.batch_alter_table("test_cases") as table:
            table.drop_column(column)

    op.drop_index("ix_test_plans_scheme_id", table_name="test_plans")
    for column in ("scheme_version_id", "scheme_id"):
        with op.batch_alter_table("test_plans") as table:
            table.drop_column(column)

    op.drop_index("ix_test_scheme_versions_project_id", table_name="test_scheme_versions")
    op.drop_index("ix_test_scheme_versions_scheme_id", table_name="test_scheme_versions")
    op.drop_table("test_scheme_versions")

    op.drop_index("ix_test_schemes_owner_id", table_name="test_schemes")
    op.drop_index("ix_test_schemes_status", table_name="test_schemes")
    op.drop_index("ix_test_schemes_project_id", table_name="test_schemes")
    op.drop_table("test_schemes")
