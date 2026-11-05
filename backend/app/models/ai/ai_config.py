"""Skill catalogue, per-project action bindings and project AI defaults (R2)."""

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# Actions that can be bound to a skill/model pair. Keep aligned with the plan.
AI_ACTIONS = (
    "case_design",
    "scheme",
    "plan",
    "review",
    "defect_analysis",
    "defect_submit",
    "regression_report",
)

AI_MODES = ("skill", "model")


class AiSkillVersion(Base, TimestampMixin):
    """One immutable version of a skill (built-in or imported SKILL.md)."""

    __tablename__ = "ai_skill_versions"
    __table_args__ = (UniqueConstraint("skill_key", "version", name="uq_skill_key_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    skill_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    resource_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_json: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")


class ProjectAiActionBinding(Base, TimestampMixin):
    """Project-level default for one AI action (mode + skill/model)."""

    __tablename__ = "project_ai_action_bindings"
    __table_args__ = (UniqueConstraint("project_id", "action", name="uq_project_action_binding"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="model")
    skill_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_skill_versions.id", ondelete="SET NULL"), nullable=True
    )
    model_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    params_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    skill_version = relationship("AiSkillVersion")
    model_profile = relationship("SystemLlmProfile")


class ProjectAiDefault(Base, TimestampMixin):
    """Project-wide fallback used when an action has no explicit binding."""

    __tablename__ = "project_ai_defaults"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    default_model_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    default_model_profile = relationship("SystemLlmProfile")


__all__ = [
    "AI_ACTIONS",
    "AI_MODES",
    "AiSkillVersion",
    "ProjectAiActionBinding",
    "ProjectAiDefault",
]
