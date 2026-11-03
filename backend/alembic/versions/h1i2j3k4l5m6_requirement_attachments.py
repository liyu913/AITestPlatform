"""requirement multi-attachments; migrate old source file columns

Revision ID: h1i2j3k4l5m6
Revises: g0h1i2j3k4l5
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h1i2j3k4l5m6"
down_revision: Union[str, Sequence[str], None] = "g0h1i2j3k4l5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "requirement_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("uploader_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_requirement_attachments_requirement_id"),
        "requirement_attachments",
        ["requirement_id"],
        unique=False,
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, source_filename, source_mime, storage_key, size_bytes, creator_id "
            "FROM requirements WHERE storage_key IS NOT NULL AND storage_key != ''"
        )
    ).fetchall()
    for row in rows:
        req_id, filename, mime, storage_key, size_bytes, uploader_id = row
        conn.execute(
            sa.text(
                "INSERT INTO requirement_attachments "
                "(requirement_id, filename, mime, storage_key, size_bytes, uploader_id) "
                "VALUES (:requirement_id, :filename, :mime, :storage_key, :size_bytes, :uploader_id)"
            ),
            {
                "requirement_id": req_id,
                "filename": filename or "document",
                "mime": mime or "application/octet-stream",
                "storage_key": storage_key,
                "size_bytes": size_bytes or 0,
                "uploader_id": uploader_id,
            },
        )

    op.drop_column("requirements", "source_filename")
    op.drop_column("requirements", "source_mime")
    op.drop_column("requirements", "storage_key")
    op.drop_column("requirements", "size_bytes")


def downgrade() -> None:
    op.add_column("requirements", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("requirements", sa.Column("storage_key", sa.String(length=1024), nullable=True))
    op.add_column("requirements", sa.Column("source_mime", sa.String(length=255), nullable=True))
    op.add_column("requirements", sa.Column("source_filename", sa.String(length=512), nullable=True))

    conn = op.get_bind()
    # Restore first attachment per requirement into source columns.
    rows = conn.execute(
        sa.text(
            "SELECT ra.requirement_id, ra.filename, ra.mime, ra.storage_key, ra.size_bytes "
            "FROM requirement_attachments ra "
            "INNER JOIN ("
            "  SELECT requirement_id, MIN(id) AS mid FROM requirement_attachments GROUP BY requirement_id"
            ") t ON ra.id = t.mid"
        )
    ).fetchall()
    for row in rows:
        req_id, filename, mime, storage_key, size_bytes = row
        conn.execute(
            sa.text(
                "UPDATE requirements SET source_filename=:filename, source_mime=:mime, "
                "storage_key=:storage_key, size_bytes=:size_bytes WHERE id=:id"
            ),
            {
                "id": req_id,
                "filename": filename,
                "mime": mime,
                "storage_key": storage_key,
                "size_bytes": size_bytes,
            },
        )

    op.drop_index(
        op.f("ix_requirement_attachments_requirement_id"),
        table_name="requirement_attachments",
    )
    op.drop_table("requirement_attachments")
