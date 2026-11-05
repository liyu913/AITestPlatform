"""R2: skill catalogue, project action bindings and AI call configuration trace.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
"""

from alembic import op
import sqlalchemy as sa

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None

BUILTIN_RESOURCE = """# 用例设计 Skill（内置）

输入：需求/计划/方案上下文、历史用例与用户指令。
输出：结构化用例草稿，必须包含每一步的动作与预期，以及设计依据（design_trace）。
约束：不得生成无预期的步骤；无法确定时标记待确认项，而不是编造断言。
"""


def upgrade() -> None:
    op.create_table(
        "ai_skill_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("skill_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("output_schema", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("resource_text", sa.Text(), nullable=True),
        sa.Column("tools_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("skill_key", "version", name="uq_skill_key_version"),
    )
    op.create_index("ix_ai_skill_versions_project_id", "ai_skill_versions", ["project_id"])
    op.create_index("ix_ai_skill_versions_skill_key", "ai_skill_versions", ["skill_key"])
    op.create_index("ix_ai_skill_versions_action", "ai_skill_versions", ["action"])

    op.create_table(
        "project_ai_action_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="model"),
        sa.Column(
            "skill_version_id",
            sa.Integer(),
            sa.ForeignKey("ai_skill_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "model_profile_id",
            sa.Integer(),
            sa.ForeignKey("system_llm_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("params_json", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "updated_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("project_id", "action", name="uq_project_action_binding"),
    )
    op.create_index(
        "ix_project_ai_action_bindings_project_id", "project_ai_action_bindings", ["project_id"]
    )

    op.create_table(
        "project_ai_defaults",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "default_model_profile_id",
            sa.Integer(),
            sa.ForeignKey("system_llm_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_project_ai_defaults_project_id", "project_ai_defaults", ["project_id"])

    inspector = sa.inspect(op.get_bind())
    if "ai_call_logs" in inspector.get_table_names():
        for column in (
            sa.Column("action", sa.String(32), nullable=True),
            sa.Column(
                "skill_version_id",
                sa.Integer(),
                sa.ForeignKey("ai_skill_versions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("binding_id", sa.Integer(), nullable=True),
            sa.Column("config_source", sa.String(16), nullable=True),
            sa.Column("source_type", sa.String(32), nullable=True),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("resolved_config", sa.JSON(none_as_null=True), nullable=True),
        ):
            with op.batch_alter_table("ai_call_logs") as table:
                table.add_column(column)
        op.create_index("ix_ai_call_logs_action", "ai_call_logs", ["action"])

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO ai_skill_versions "
            "(project_id, skill_key, name, action, version, description, resource_text, enabled) "
            "VALUES (NULL, :key, :name, :action, 1, :desc, :res, :enabled)"
        ),
        {
            "key": "test-case-generator",
            "name": "用例设计（内置）",
            "action": "case_design",
            "desc": "按需求与方案生成结构化用例草稿，强制每步预期与设计依据。",
            "res": BUILTIN_RESOURCE,
            "enabled": True,
        },
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ai_call_logs" in inspector.get_table_names():
        op.drop_index("ix_ai_call_logs_action", table_name="ai_call_logs")
        for column in (
            "resolved_config",
            "source_id",
            "source_type",
            "config_source",
            "binding_id",
            "skill_version_id",
            "action",
        ):
            with op.batch_alter_table("ai_call_logs") as table:
                table.drop_column(column)

    op.drop_index("ix_project_ai_defaults_project_id", table_name="project_ai_defaults")
    op.drop_table("project_ai_defaults")

    op.drop_index(
        "ix_project_ai_action_bindings_project_id", table_name="project_ai_action_bindings"
    )
    op.drop_table("project_ai_action_bindings")

    op.drop_index("ix_ai_skill_versions_action", table_name="ai_skill_versions")
    op.drop_index("ix_ai_skill_versions_skill_key", table_name="ai_skill_versions")
    op.drop_index("ix_ai_skill_versions_project_id", table_name="ai_skill_versions")
    op.drop_table("ai_skill_versions")
