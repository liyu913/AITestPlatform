"""Resolve the AI configuration for one action (R2).

Resolution order, upper levels override lower ones field by field:
call override -> project action binding -> project default -> system default.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.ai.ai_config import (
    AI_ACTIONS,
    AI_MODES,
    AiSkillVersion,
    ProjectAiActionBinding,
    ProjectAiDefault,
)
from app.models.ai.system_ai_settings import SystemLlmProfile
from app.services.ai import call_context


@dataclass
class ResolvedAiConfig:
    action: str
    mode: str
    source: str
    binding_id: int | None = None
    skill_version_id: int | None = None
    skill_key: str | None = None
    skill_name: str | None = None
    skill_version: int | None = None
    model_profile_id: int | None = None
    model_name: str | None = None
    api_base: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "mode": self.mode,
            "source": self.source,
            "binding_id": self.binding_id,
            "skill_version_id": self.skill_version_id,
            "skill_key": self.skill_key,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "model_profile_id": self.model_profile_id,
            "model_name": self.model_name,
            "api_base": self.api_base,
            "params": self.params,
        }


def _validate_action(action: str) -> str:
    cleaned = (action or "").strip()
    if cleaned not in AI_ACTIONS:
        raise AppError("bad_request", f"未知的 AI 动作：{action}", status_code=400)
    return cleaned


def _active_system_profile(db: Session) -> SystemLlmProfile | None:
    return (
        db.query(SystemLlmProfile)
        .filter(SystemLlmProfile.is_active.is_(True))
        .order_by(SystemLlmProfile.id.asc())
        .first()
    )


def _load_skill(db: Session, *, project_id: int, skill_version_id: int) -> AiSkillVersion:
    skill = db.get(AiSkillVersion, skill_version_id)
    if skill is None or (skill.project_id not in (None, project_id)):
        raise AppError("not_found", "Skill 版本不存在", status_code=404)
    if not skill.enabled:
        raise AppError("conflict", "该 Skill 版本已停用", status_code=409)
    return skill


def _load_model(db: Session, model_profile_id: int | None) -> SystemLlmProfile | None:
    if model_profile_id is None:
        return None
    profile = db.get(SystemLlmProfile, model_profile_id)
    if profile is None:
        raise AppError("not_found", "模型配置不存在", status_code=404)
    return profile


def resolve(
    db: Session,
    *,
    project_id: int,
    action: str,
    override: dict[str, Any] | None = None,
) -> ResolvedAiConfig:
    action = _validate_action(action)
    override = override or {}

    binding = (
        db.query(ProjectAiActionBinding)
        .filter(
            ProjectAiActionBinding.project_id == project_id,
            ProjectAiActionBinding.action == action,
        )
        .first()
    )
    default = (
        db.query(ProjectAiDefault)
        .filter(ProjectAiDefault.project_id == project_id)
        .first()
    )
    system = _active_system_profile(db)

    mode = str(override.get("mode") or (binding.mode if binding else "model")).strip()
    if mode not in AI_MODES:
        raise AppError("bad_request", f"未知的调用方式：{mode}", status_code=400)

    skill_version_id = (
        override.get("skill_version_id")
        if override.get("skill_version_id") is not None
        else (binding.skill_version_id if binding else None)
    )
    model_profile_id = (
        override.get("model_profile_id")
        if override.get("model_profile_id") is not None
        else (binding.model_profile_id if binding else None)
    )
    if model_profile_id is None and default is not None:
        model_profile_id = default.default_model_profile_id

    if override.get("mode") or override.get("skill_version_id") or override.get("model_profile_id"):
        source = "call"
    elif binding is not None:
        source = "action"
    elif default is not None and model_profile_id == default.default_model_profile_id:
        source = "project"
    else:
        source = "system"

    skill: AiSkillVersion | None = None
    if mode == "skill":
        if skill_version_id is None:
            raise AppError("bad_request", "Skill 模式需要选择 Skill 版本", status_code=400)
        skill = _load_skill(db, project_id=project_id, skill_version_id=skill_version_id)
    else:
        skill_version_id = None

    profile = _load_model(db, model_profile_id) or (system if model_profile_id is None else None)
    if profile is None:
        raise AppError("bad_request", "项目未配置可用模型，请先在 AI 设置中选择模型", status_code=400)

    params: dict[str, Any] = {}
    if binding is not None and isinstance(binding.params_json, dict):
        params.update(binding.params_json)
    if isinstance(override.get("params"), dict):
        params.update(override["params"])

    return ResolvedAiConfig(
        action=action,
        mode=mode,
        source=source,
        binding_id=binding.id if binding else None,
        skill_version_id=skill.id if skill else None,
        skill_key=skill.skill_key if skill else None,
        skill_name=skill.name if skill else None,
        skill_version=skill.version if skill else None,
        model_profile_id=profile.id,
        model_name=profile.model,
        api_base=profile.api_base,
        params=params,
    )


@contextmanager
def bind_context(config: ResolvedAiConfig) -> Iterator[ResolvedAiConfig]:
    """Expose the resolved configuration to AI call logging for this call."""
    tokens = [
        call_context.action_var.set(config.action),
        call_context.skill_version_id_var.set(config.skill_version_id),
        call_context.binding_id_var.set(config.binding_id),
        call_context.config_source_var.set(config.source),
        call_context.resolved_config_var.set(config.as_dict()),
    ]
    try:
        yield config
    finally:
        reset_context(tokens)


def push_context(config: ResolvedAiConfig) -> list:
    """Set the AI config context without a scope (generators reset it manually)."""
    return [
        call_context.action_var.set(config.action),
        call_context.skill_version_id_var.set(config.skill_version_id),
        call_context.binding_id_var.set(config.binding_id),
        call_context.config_source_var.set(config.source),
        call_context.resolved_config_var.set(config.as_dict()),
    ]


def reset_context(tokens: list) -> None:
    for var, token in zip(
        (
            call_context.action_var,
            call_context.skill_version_id_var,
            call_context.binding_id_var,
            call_context.config_source_var,
            call_context.resolved_config_var,
        ),
        tokens,
    ):
        var.reset(token)


def upsert_binding(
    db: Session,
    *,
    project_id: int,
    action: str,
    mode: str,
    skill_version_id: int | None,
    model_profile_id: int | None,
    params: dict[str, Any] | None,
    user_id: int | None,
) -> ProjectAiActionBinding:
    action = _validate_action(action)
    mode = (mode or "model").strip()
    if mode not in AI_MODES:
        raise AppError("bad_request", f"未知的调用方式：{mode}", status_code=400)
    if mode == "skill":
        if skill_version_id is None:
            raise AppError("bad_request", "Skill 模式需要选择 Skill 版本", status_code=400)
        _load_skill(db, project_id=project_id, skill_version_id=skill_version_id)
    else:
        skill_version_id = None
    if model_profile_id is not None:
        _load_model(db, model_profile_id)

    binding = (
        db.query(ProjectAiActionBinding)
        .filter(
            ProjectAiActionBinding.project_id == project_id,
            ProjectAiActionBinding.action == action,
        )
        .first()
    )
    if binding is None:
        binding = ProjectAiActionBinding(project_id=project_id, action=action)
        db.add(binding)
    binding.mode = mode
    binding.skill_version_id = skill_version_id
    binding.model_profile_id = model_profile_id
    binding.params_json = params or None
    binding.updated_by_id = user_id
    db.commit()
    db.refresh(binding)
    return binding


def upsert_default(
    db: Session,
    *,
    project_id: int,
    default_model_profile_id: int | None,
    user_id: int | None,
) -> ProjectAiDefault:
    if default_model_profile_id is not None:
        _load_model(db, default_model_profile_id)
    row = db.query(ProjectAiDefault).filter(ProjectAiDefault.project_id == project_id).first()
    if row is None:
        row = ProjectAiDefault(project_id=project_id)
        db.add(row)
    row.default_model_profile_id = default_model_profile_id
    row.updated_by_id = user_id
    db.commit()
    db.refresh(row)
    return row


def list_skills(
    db: Session, *, project_id: int | None = None, action: str | None = None
) -> list[AiSkillVersion]:
    query = db.query(AiSkillVersion).filter(AiSkillVersion.enabled.is_(True))
    if project_id is not None:
        query = query.filter(
            (AiSkillVersion.project_id.is_(None)) | (AiSkillVersion.project_id == project_id)
        )
    if action:
        query = query.filter(AiSkillVersion.action == action)
    return query.order_by(AiSkillVersion.skill_key.asc(), AiSkillVersion.version.desc()).all()


__all__ = [
    "AI_ACTIONS",
    "ResolvedAiConfig",
    "bind_context",
    "list_skills",
    "resolve",
    "upsert_binding",
    "upsert_default",
]
