"""test_cases / ui_cases variables_json

Revision ID: d7e8f9g0h1i2
Revises: c6d7e8f9g0h1
Create Date: 2026-10-23

"""
from __future__ import annotations

import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9g0h1i2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9g0h1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KV_SPLIT = re.compile(r"[;；\n|/]+")
_PAIR = re.compile(
    r"^\s*"
    r"(?:\{\{\s*)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\}\})?"
    r"\s*[：:=\-]\s*"
    r"(.+?)\s*$"
)


def _parse_test_data(text: str | None) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for part in _KV_SPLIT.split(raw):
        chunk = part.strip()
        if not chunk:
            continue
        m = _PAIR.match(chunk)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key and value:
            out[key] = value
    return out


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("variables_json", sa.JSON(), nullable=True))
    op.add_column("ui_cases", sa.Column("variables_json", sa.JSON(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, test_data FROM test_cases")).fetchall()
    for row in rows:
        case_id, test_data = row[0], row[1]
        parsed = _parse_test_data(test_data)
        if not parsed:
            continue
        bind.execute(
            sa.text("UPDATE test_cases SET variables_json = :vars WHERE id = :id"),
            {"vars": json.dumps(parsed, ensure_ascii=False), "id": case_id},
        )


def downgrade() -> None:
    op.drop_column("ui_cases", "variables_json")
    op.drop_column("test_cases", "variables_json")
