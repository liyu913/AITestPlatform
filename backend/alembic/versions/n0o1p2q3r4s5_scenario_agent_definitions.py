"""scenario agent: definition candidates + chat messages

Revision ID: n0o1p2q3r4s5
Revises: m0n1o2p3q4r5
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n0o1p2q3r4s5"
down_revision: Union[str, Sequence[str], None] = "m0n1o2p3q4r5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _cols("api_scenario_agent_sessions")
    if "candidate_definition_ids_json" not in cols:
        op.add_column(
            "api_scenario_agent_sessions",
            sa.Column("candidate_definition_ids_json", sa.JSON(), nullable=True),
        )
    if "messages_json" not in cols:
        op.add_column(
            "api_scenario_agent_sessions",
            sa.Column("messages_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    cols = _cols("api_scenario_agent_sessions")
    if "messages_json" in cols:
        op.drop_column("api_scenario_agent_sessions", "messages_json")
    if "candidate_definition_ids_json" in cols:
        op.drop_column("api_scenario_agent_sessions", "candidate_definition_ids_json")
