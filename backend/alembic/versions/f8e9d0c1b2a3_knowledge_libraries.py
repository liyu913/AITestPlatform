"""knowledge_libraries

Revision ID: f8e9d0c1b2a3
Revises: e7f8a9b0c1d2
Create Date: 2026-05-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8e9d0c1b2a3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_libraries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_libraries_project_id"), "knowledge_libraries", ["project_id"], unique=False)

    op.add_column(
        "knowledge_documents",
        sa.Column("library_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_knowledge_documents_library_id_knowledge_libraries"),
        "knowledge_documents",
        "knowledge_libraries",
        ["library_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_knowledge_documents_library_id"), "knowledge_documents", ["library_id"], unique=False)

    op.execute(
        sa.text(
            """
            INSERT INTO knowledge_libraries (project_id, name, description)
            SELECT id, '默认知识库', NULL FROM projects
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE knowledge_documents kd
            INNER JOIN knowledge_libraries kl
              ON kl.project_id = kd.project_id AND kl.name = '默认知识库'
            SET kd.library_id = kl.id
            WHERE kd.library_id IS NULL
            """
        )
    )

    op.alter_column("knowledge_documents", "library_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.drop_constraint(op.f("fk_knowledge_documents_library_id_knowledge_libraries"), "knowledge_documents", type_="foreignkey")
    op.drop_index(op.f("ix_knowledge_documents_library_id"), table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "library_id")
    op.drop_index(op.f("ix_knowledge_libraries_project_id"), table_name="knowledge_libraries")
    op.drop_table("knowledge_libraries")
