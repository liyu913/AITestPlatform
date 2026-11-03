"""rebuild project_modules + api_definitions.module_id

Revision ID: l5m6n7o8p9q0
Revises: k4l5m6n7o8p9
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l5m6n7o8p9q0"
down_revision: Union[str, Sequence[str], None] = "k4l5m6n7o8p9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_modules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("creator_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_id"], ["project_modules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "parent_id", "name", name="uq_project_module_parent_name"),
    )
    op.create_index("ix_project_modules_project_id", "project_modules", ["project_id"])
    op.create_index("ix_project_modules_parent_id", "project_modules", ["parent_id"])

    op.add_column("api_definitions", sa.Column("module_id", sa.Integer(), nullable=True))
    op.create_index("ix_api_definitions_module_id", "api_definitions", ["module_id"])
    op.create_foreign_key(
        "fk_api_definitions_module_id_project_modules",
        "api_definitions",
        "project_modules",
        ["module_id"],
        ["id"],
        ondelete="SET NULL",
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT DISTINCT project_id, module FROM api_definitions "
            "WHERE module IS NOT NULL AND TRIM(module) != ''"
        )
    ).fetchall()
    seen: set[tuple[int, str]] = set()
    for project_id, module_name in rows:
        name = str(module_name).strip()
        if not name:
            continue
        key = (int(project_id), name)
        if key in seen:
            continue
        seen.add(key)
        conn.execute(
            sa.text(
                "INSERT INTO project_modules (project_id, parent_id, name, description, sort_order) "
                "VALUES (:pid, NULL, :name, '', 0)"
            ),
            {"pid": int(project_id), "name": name},
        )
        mid = conn.execute(
            sa.text(
                "SELECT id FROM project_modules "
                "WHERE project_id = :pid AND parent_id IS NULL AND name = :name "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"pid": int(project_id), "name": name},
        ).scalar()
        if mid is None:
            continue
        conn.execute(
            sa.text(
                "UPDATE api_definitions SET module_id = :mid "
                "WHERE project_id = :pid AND module = :name AND module_id IS NULL"
            ),
            {"mid": int(mid), "pid": int(project_id), "name": name},
        )


def downgrade() -> None:
    op.drop_constraint("fk_api_definitions_module_id_project_modules", "api_definitions", type_="foreignkey")
    op.drop_index("ix_api_definitions_module_id", table_name="api_definitions")
    op.drop_column("api_definitions", "module_id")
    op.drop_index("ix_project_modules_parent_id", table_name="project_modules")
    op.drop_index("ix_project_modules_project_id", table_name="project_modules")
    op.drop_table("project_modules")
