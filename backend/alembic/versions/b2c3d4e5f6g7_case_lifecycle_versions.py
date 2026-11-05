"""Split case lifecycle from execution result, add version history (B2).

Existing ``test_cases.status`` mixed authoring state and the last execution
outcome. This migration:

* adds ``lifecycle_status`` / ``last_result`` / ``last_result_at`` / ``version_no``
  (plus archive bookkeeping) to ``test_cases``;
* backfills both axes from the legacy ``status`` and keeps ``status`` as a mirror;
* adds immutable version tables for cases and requirements and seeds a baseline
  version for every existing asset so history starts from a known point.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6g7"
down_revision = "91de45b6c284"
branch_labels = None
depends_on = None

_RESULT_WORDS = ("passed", "failed", "blocked", "error")
_LIFECYCLE_WORDS = ("draft", "pending_review", "confirmed", "archived")


def _dumps(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _seed_case_versions(bind: sa.engine.Connection) -> None:
    case_rows = bind.execute(
        sa.text(
            """
            SELECT id, project_id, title, module, precondition, test_data,
                   case_type, requirement_id, overall_expected, priority,
                   lifecycle_status, tags_json, design_trace, ai_draft_id, version_no
            FROM test_cases
            """
        )
    ).mappings().all()
    if not case_rows:
        return
    step_rows = bind.execute(
        sa.text("SELECT case_id, `order`, action, expected FROM test_case_steps ORDER BY case_id, `order`")
    ).mappings().all()
    steps_by_case: dict[int, list[dict]] = {}
    for row in step_rows:
        steps_by_case.setdefault(int(row["case_id"]), []).append(
            {
                "order": int(row["order"] or 0),
                "action": row["action"],
                "expected": row["expected"],
            }
        )

    insert = sa.text(
        """
        INSERT INTO test_case_versions
            (case_id, project_id, version_no, lifecycle_status, snapshot,
             change_source, change_note, created_at, updated_at)
        VALUES
            (:case_id, :project_id, :version_no, :lifecycle_status, :snapshot,
             'migration', '迁移基准版本', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    for row in case_rows:
        case_id = int(row["id"])
        snapshot = {
            "title": row["title"],
            "module": row["module"],
            "precondition": row["precondition"],
            "test_data": row["test_data"],
            "case_type": row["case_type"],
            "overall_expected": row["overall_expected"],
            "priority": row["priority"],
            "requirement_id": row["requirement_id"],
            "tags": row["tags_json"] if isinstance(row["tags_json"], list) else None,
            "variables": None,
            "design_trace": row["design_trace"] if isinstance(row["design_trace"], dict) else None,
            "ai_draft_id": row["ai_draft_id"],
            "lifecycle_status": row["lifecycle_status"],
            "steps": steps_by_case.get(case_id, []),
        }
        bind.execute(
            insert,
            {
                "case_id": case_id,
                "project_id": int(row["project_id"]),
                "version_no": int(row["version_no"] or 1),
                "lifecycle_status": row["lifecycle_status"] or "draft",
                "snapshot": _dumps(snapshot),
            },
        )


def _seed_requirement_versions(bind: sa.engine.Connection) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, project_id, title, body_md, status, priority,
                   acceptance_rules, version_no
            FROM requirements
            """
        )
    ).mappings().all()
    if not rows:
        return
    insert = sa.text(
        """
        INSERT INTO requirement_versions
            (requirement_id, project_id, version_no, snapshot, change_source,
             change_note, created_at, updated_at)
        VALUES
            (:requirement_id, :project_id, :version_no, :snapshot, 'migration',
             '迁移基准版本', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    for row in rows:
        snapshot = {
            "title": row["title"],
            "body_md": row["body_md"],
            "status": row["status"],
            "priority": row["priority"],
            "acceptance_rules": row["acceptance_rules"],
        }
        bind.execute(
            insert,
            {
                "requirement_id": int(row["id"]),
                "project_id": int(row["project_id"]),
                "version_no": int(row["version_no"] or 1),
                "snapshot": _dumps(snapshot),
            },
        )


def upgrade() -> None:
    # One column per batch: adding several columns (one of them a foreign key)
    # in a single SQLite batch rebuild trips Alembic's dependency sort.
    for column in (
        sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("last_result", sa.String(32), nullable=True),
        sa.Column("last_result_at", sa.DateTime(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column(
            "archived_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    ):
        with op.batch_alter_table("test_cases") as table:
            table.add_column(column)
    op.create_index("ix_test_cases_lifecycle_status", "test_cases", ["lifecycle_status"])
    op.create_index("ix_test_cases_last_result", "test_cases", ["last_result"])

    # MySQL cannot give a TEXT column a DEFAULT value (error 1101), so the
    # column is added nullable, backfilled and then made NOT NULL.
    with op.batch_alter_table("requirements") as table:
        table.add_column(sa.Column("acceptance_rules", sa.Text(), nullable=True))
    with op.batch_alter_table("requirements") as table:
        table.add_column(sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"))
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE requirements SET acceptance_rules = '' WHERE acceptance_rules IS NULL"))
    with op.batch_alter_table("requirements") as table:
        table.alter_column("acceptance_rules", existing_type=sa.Text(), nullable=False)

    op.create_table(
        "test_case_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("change_source", sa.String(32), nullable=False, server_default="edit"),
        sa.Column("change_note", sa.String(512), nullable=True),
        sa.Column(
            "created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("case_id", "version_no", name="uq_test_case_version_no"),
    )
    op.create_index("ix_test_case_versions_case_id", "test_case_versions", ["case_id"])
    op.create_index("ix_test_case_versions_project_id", "test_case_versions", ["project_id"])

    op.create_table(
        "test_case_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("test_case_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(16), nullable=False, server_default="approved"),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "reviewer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_test_case_reviews_case_id", "test_case_reviews", ["case_id"])
    op.create_index("ix_test_case_reviews_project_id", "test_case_reviews", ["project_id"])

    op.create_table(
        "requirement_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("change_source", sa.String(32), nullable=False, server_default="edit"),
        sa.Column("change_note", sa.String(512), nullable=True),
        sa.Column(
            "created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("requirement_id", "version_no", name="uq_requirement_version_no"),
    )
    op.create_index("ix_requirement_versions_requirement_id", "requirement_versions", ["requirement_id"])
    op.create_index("ix_requirement_versions_project_id", "requirement_versions", ["project_id"])

    # --- backfill the two axes from the legacy merged column ---
    result_list = ", ".join(f"'{w}'" for w in _RESULT_WORDS)
    lifecycle_list = ", ".join(f"'{w}'" for w in _LIFECYCLE_WORDS)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE test_cases
               SET lifecycle_status = CASE
                     WHEN LOWER(status) IN ({result_list}) THEN 'confirmed'
                     WHEN LOWER(status) IN ({lifecycle_list}) THEN LOWER(status)
                     ELSE 'draft'
                   END,
                   last_result = CASE
                     WHEN LOWER(status) IN ({result_list}) THEN LOWER(status)
                     ELSE NULL
                   END,
                   last_result_at = CASE
                     WHEN LOWER(status) IN ({result_list}) THEN updated_at
                     ELSE NULL
                   END
            """
        )
    )
    # ``status`` stays as the legacy mirror of the lifecycle.
    bind.execute(sa.text("UPDATE test_cases SET status = lifecycle_status"))

    _seed_case_versions(bind)
    _seed_requirement_versions(bind)


def downgrade() -> None:
    op.drop_index("ix_requirement_versions_project_id", table_name="requirement_versions")
    op.drop_index("ix_requirement_versions_requirement_id", table_name="requirement_versions")
    op.drop_table("requirement_versions")
    op.drop_index("ix_test_case_reviews_project_id", table_name="test_case_reviews")
    op.drop_index("ix_test_case_reviews_case_id", table_name="test_case_reviews")
    op.drop_table("test_case_reviews")
    op.drop_index("ix_test_case_versions_project_id", table_name="test_case_versions")
    op.drop_index("ix_test_case_versions_case_id", table_name="test_case_versions")
    op.drop_table("test_case_versions")

    with op.batch_alter_table("requirements") as table:
        table.drop_column("version_no")
        table.drop_column("acceptance_rules")

    op.drop_index("ix_test_cases_last_result", table_name="test_cases")
    op.drop_index("ix_test_cases_lifecycle_status", table_name="test_cases")
    for name in (
        "archived_by_id",
        "archived_at",
        "version_no",
        "last_result_at",
        "last_result",
        "lifecycle_status",
    ):
        with op.batch_alter_table("test_cases") as table:
            table.drop_column(name)
