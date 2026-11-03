"""system llm / embedding profiles

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p3q4r5s6t7u8"
down_revision: Union[str, Sequence[str], None] = "o2p3q4r5s6t7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_llm_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("api_base", sa.String(length=1024), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("allowed_models", sa.Text(), nullable=True),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_system_llm_profiles_name"),
    )
    op.create_table(
        "system_embedding_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("api_base", sa.String(length=1024), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("dim", sa.Integer(), nullable=True),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_system_embedding_profiles_name"),
    )

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "system_ai_settings" in inspector.get_table_names():
        rows = conn.execute(
            sa.text(
                "SELECT llm_api_base, llm_model, llm_allowed_models, llm_api_key_enc, "
                "embedding_api_base, embedding_model, embedding_dim, embedding_api_key_enc "
                "FROM system_ai_settings WHERE id = 1"
            )
        ).fetchall()
        if rows:
            r = rows[0]
            llm_any = any(
                [
                    (r[0] or "").strip() if r[0] else False,
                    (r[1] or "").strip() if r[1] else False,
                    (r[2] or "").strip() if r[2] else False,
                    (r[3] or "").strip() if r[3] else False,
                ]
            )
            if llm_any:
                conn.execute(
                    sa.text(
                        "INSERT INTO system_llm_profiles "
                        "(name, api_base, model, allowed_models, api_key_enc, is_active) "
                        "VALUES (:name, :api_base, :model, :allowed_models, :api_key_enc, 1)"
                    ),
                    {
                        "name": "默认",
                        "api_base": r[0],
                        "model": r[1],
                        "allowed_models": r[2],
                        "api_key_enc": r[3],
                    },
                )
            emb_any = any(
                [
                    (r[4] or "").strip() if r[4] else False,
                    (r[5] or "").strip() if r[5] else False,
                    r[6] is not None,
                    (r[7] or "").strip() if r[7] else False,
                ]
            )
            if emb_any:
                conn.execute(
                    sa.text(
                        "INSERT INTO system_embedding_profiles "
                        "(name, api_base, model, dim, api_key_enc, is_active) "
                        "VALUES (:name, :api_base, :model, :dim, :api_key_enc, 1)"
                    ),
                    {
                        "name": "默认",
                        "api_base": r[4],
                        "model": r[5],
                        "dim": r[6],
                        "api_key_enc": r[7],
                    },
                )
        op.drop_table("system_ai_settings")


def downgrade() -> None:
    op.create_table(
        "system_ai_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("llm_api_base", sa.String(length=1024), nullable=True),
        sa.Column("llm_model", sa.String(length=256), nullable=True),
        sa.Column("llm_allowed_models", sa.Text(), nullable=True),
        sa.Column("llm_api_key_enc", sa.Text(), nullable=True),
        sa.Column("embedding_api_base", sa.String(length=1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=256), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_api_key_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO system_ai_settings (id) VALUES (1)"))
    llm = conn.execute(
        sa.text(
            "SELECT api_base, model, allowed_models, api_key_enc "
            "FROM system_llm_profiles WHERE is_active = 1 ORDER BY id LIMIT 1"
        )
    ).fetchone()
    if llm is None:
        llm = conn.execute(
            sa.text(
                "SELECT api_base, model, allowed_models, api_key_enc "
                "FROM system_llm_profiles ORDER BY id LIMIT 1"
            )
        ).fetchone()
    emb = conn.execute(
        sa.text(
            "SELECT api_base, model, dim, api_key_enc "
            "FROM system_embedding_profiles WHERE is_active = 1 ORDER BY id LIMIT 1"
        )
    ).fetchone()
    if emb is None:
        emb = conn.execute(
            sa.text(
                "SELECT api_base, model, dim, api_key_enc "
                "FROM system_embedding_profiles ORDER BY id LIMIT 1"
            )
        ).fetchone()
    if llm or emb:
        conn.execute(
            sa.text(
                "UPDATE system_ai_settings SET "
                "llm_api_base=:lb, llm_model=:lm, llm_allowed_models=:la, llm_api_key_enc=:lk, "
                "embedding_api_base=:eb, embedding_model=:em, embedding_dim=:ed, embedding_api_key_enc=:ek "
                "WHERE id=1"
            ),
            {
                "lb": llm[0] if llm else None,
                "lm": llm[1] if llm else None,
                "la": llm[2] if llm else None,
                "lk": llm[3] if llm else None,
                "eb": emb[0] if emb else None,
                "em": emb[1] if emb else None,
                "ed": emb[2] if emb else None,
                "ek": emb[3] if emb else None,
            },
        )
    op.drop_table("system_embedding_profiles")
    op.drop_table("system_llm_profiles")
