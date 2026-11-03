"""drop project modules and module_id columns

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r5s6t7u8v9w0"
down_revision: Union[str, Sequence[str], None] = "q4r5s6t7u8v9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MODULE_TABLES = ("test_cases", "api_cases", "api_definitions", "ui_cases", "app_cases")


def _drop_fk_if_exists(table: str, name: str) -> None:
    bind = op.get_bind()
    fks = sa.inspect(bind).get_foreign_keys(table)
    if any(fk.get("name") == name for fk in fks):
        op.drop_constraint(name, table, type_="foreignkey")


def _drop_index_if_exists(table: str, name: str) -> None:
    bind = op.get_bind()
    indexes = sa.inspect(bind).get_indexes(table)
    if any(ix.get("name") == name for ix in indexes):
        op.drop_index(name, table_name=table)


def _drop_column_if_exists(table: str, column: str) -> None:
    bind = op.get_bind()
    columns = sa.inspect(bind).get_columns(table)
    if any(col["name"] == column for col in columns):
        op.drop_column(table, column)


def upgrade() -> None:
    for table in _MODULE_TABLES:
        _drop_fk_if_exists(table, f"fk_{table}_module_id_project_modules")
        _drop_index_if_exists(table, f"ix_{table}_module_id")
        _drop_column_if_exists(table, "module_id")
    bind = op.get_bind()
    if sa.inspect(bind).has_table("project_modules"):
        op.drop_table("project_modules")


def downgrade() -> None:
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
    for table in _MODULE_TABLES:
        op.add_column(table, sa.Column("module_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_module_id", table, ["module_id"])
        op.create_foreign_key(
            f"fk_{table}_module_id_project_modules",
            table,
            "project_modules",
            ["module_id"],
            ["id"],
            ondelete="SET NULL",
        )
