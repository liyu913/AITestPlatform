import re

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role, require_superuser
from app.errors import AppError
from app.models.ai.ai_config import (
    AI_ACTIONS,
    AiSkillVersion,
    ProjectAiActionBinding,
    ProjectAiDefault,
)
from app.models.ai.system_ai_settings import SystemLlmProfile
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.schemas.ai.ai_config import (
    AiConfigOverview,
    BindingIn,
    BindingOut,
    DefaultsIn,
    DefaultsOut,
    ModelProfileRef,
    ResolvedOut,
    SkillCreate,
    SkillOut,
    SkillUpdate,
)
from app.services.ai import ai_config

router = APIRouter(prefix="/projects/{project_id}/ai-config", tags=["ai-config"])
admin_router = APIRouter(prefix="/admin/ai-skills", tags=["admin"])


def _models(db: Session) -> list[ModelProfileRef]:
    rows = db.query(SystemLlmProfile).order_by(SystemLlmProfile.id.asc()).all()
    return [
        ModelProfileRef(id=row.id, name=row.name, model=row.model, is_active=bool(row.is_active))
        for row in rows
    ]


@router.get("/", response_model=AiConfigOverview)
def get_ai_config(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> AiConfigOverview:
    bindings = (
        db.query(ProjectAiActionBinding)
        .filter(ProjectAiActionBinding.project_id == project_id)
        .order_by(ProjectAiActionBinding.action.asc())
        .all()
    )
    defaults_row = (
        db.query(ProjectAiDefault).filter(ProjectAiDefault.project_id == project_id).first()
    )
    return AiConfigOverview(
        actions=list(AI_ACTIONS),
        bindings=[BindingOut.model_validate(row) for row in bindings],
        defaults=DefaultsOut(
            project_id=project_id,
            default_model_profile_id=(
                defaults_row.default_model_profile_id if defaults_row is not None else None
            ),
        ),
        skills=[SkillOut.model_validate(row) for row in ai_config.list_skills(db, project_id=project_id)],
        models=_models(db),
    )


@router.put("/bindings/{action}", response_model=BindingOut)
def put_binding(
    project_id: int,
    action: str,
    payload: BindingIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> BindingOut:
    row = ai_config.upsert_binding(
        db,
        project_id=project_id,
        action=action,
        mode=payload.mode,
        skill_version_id=payload.skill_version_id,
        model_profile_id=payload.model_profile_id,
        params=payload.params,
        user_id=user.id,
    )
    return BindingOut.model_validate(row)


@router.delete("/bindings/{action}", status_code=204)
def delete_binding(
    project_id: int,
    action: str,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> None:
    row = (
        db.query(ProjectAiActionBinding)
        .filter(
            ProjectAiActionBinding.project_id == project_id,
            ProjectAiActionBinding.action == action,
        )
        .first()
    )
    if row is not None:
        db.delete(row)
        db.commit()


@router.put("/defaults", response_model=DefaultsOut)
def put_defaults(
    project_id: int,
    payload: DefaultsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> DefaultsOut:
    row = ai_config.upsert_default(
        db,
        project_id=project_id,
        default_model_profile_id=payload.default_model_profile_id,
        user_id=user.id,
    )
    return DefaultsOut(
        project_id=project_id, default_model_profile_id=row.default_model_profile_id
    )


@router.get("/resolve", response_model=ResolvedOut)
def resolve_config(
    project_id: int,
    action: str = Query(..., max_length=32),
    mode: str | None = Query(None, max_length=16),
    skill_version_id: int | None = Query(None, ge=1),
    model_profile_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ResolvedOut:
    override = {
        key: value
        for key, value in (
            ("mode", mode),
            ("skill_version_id", skill_version_id),
            ("model_profile_id", model_profile_id),
        )
        if value is not None
    }
    config = ai_config.resolve(db, project_id=project_id, action=action, override=override)
    return ResolvedOut(**config.as_dict())


@admin_router.get("/", response_model=list[SkillOut])
def list_all_skills(
    action: str | None = Query(None, max_length=32),
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> list[SkillOut]:
    query = db.query(AiSkillVersion)
    if action:
        query = query.filter(AiSkillVersion.action == action)
    rows = query.order_by(AiSkillVersion.skill_key.asc(), AiSkillVersion.version.desc()).all()
    return [SkillOut.model_validate(row) for row in rows]


@admin_router.post("/", response_model=SkillOut, status_code=201)
def create_skill(
    payload: SkillCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> SkillOut:
    if payload.action not in AI_ACTIONS:
        raise AppError("bad_request", f"未知的 AI 动作：{payload.action}", status_code=400)
    exists = (
        db.query(AiSkillVersion)
        .filter(
            AiSkillVersion.skill_key == payload.skill_key,
            AiSkillVersion.version == payload.version,
        )
        .first()
    )
    if exists is not None:
        raise AppError("conflict", "该 Skill 版本已存在", status_code=409)
    row = AiSkillVersion(
        project_id=payload.project_id,
        skill_key=payload.skill_key.strip(),
        name=payload.name.strip(),
        action=payload.action,
        version=payload.version,
        description=payload.description,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        resource_text=payload.resource_text,
        tools_json=payload.tools_json,
        enabled=payload.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return SkillOut.model_validate(row)


_SKILL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


def _front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta


@admin_router.post("/import", response_model=SkillOut, status_code=201)
async def import_skill(
    file: UploadFile = File(...),
    action: str = Form(...),
    skill_key: str | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    version: int | None = Form(default=None),
    tools: str | None = Form(default=None),
    project_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> SkillOut:
    if action not in AI_ACTIONS:
        raise AppError("bad_request", f"未知的 AI 动作：{action}", status_code=400)
    raw = (await file.read()).decode("utf-8", errors="replace")
    if len(raw) > 1_000_000:
        raise AppError("bad_request", "Skill 文件不能超过 1MB", status_code=400)
    meta = _front_matter(raw)
    key = (skill_key or meta.get("name") or (file.filename or "").rsplit(".", 1)[0] or "").strip()
    key = key.lower().replace(" ", "-")
    if not _SKILL_KEY_RE.match(key):
        raise AppError("bad_request", "Skill key 只能包含小写字母、数字、- 和 _", status_code=400)
    if version is None:
        current = (
            db.query(AiSkillVersion)
            .filter(AiSkillVersion.skill_key == key)
            .order_by(AiSkillVersion.version.desc())
            .first()
        )
        version = (current.version + 1) if current is not None else 1
    exists = (
        db.query(AiSkillVersion)
        .filter(AiSkillVersion.skill_key == key, AiSkillVersion.version == version)
        .first()
    )
    if exists is not None:
        raise AppError("conflict", f"Skill {key} v{version} 已存在", status_code=409)
    row = AiSkillVersion(
        project_id=project_id,
        skill_key=key,
        name=(name or meta.get("name") or key)[:256],
        action=action,
        version=version,
        description=description or meta.get("description"),
        resource_text=raw,
        tools_json=[t.strip() for t in (tools or "").split(",") if t.strip()] or None,
        enabled=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return SkillOut.model_validate(row)


@admin_router.patch("/{skill_id}", response_model=SkillOut)
def update_skill(
    skill_id: int,
    payload: SkillUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> SkillOut:
    row = db.get(AiSkillVersion, skill_id)
    if row is None:
        raise AppError("not_found", "Skill 版本不存在", status_code=404)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.description is not None:
        row.description = payload.description
    db.commit()
    db.refresh(row)
    return SkillOut.model_validate(row)
