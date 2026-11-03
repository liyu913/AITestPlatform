from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_db, require_superuser
from app.errors import AppError
from app.models.ai.system_ai_settings import SystemEmbeddingProfile, SystemLlmProfile, SystemMidsceneProfile
from app.models.identity.user import User
from app.schemas.ai.admin_ai_settings import (
    EmbeddingProfileCreate,
    EmbeddingProfileOut,
    EmbeddingProfileUpdate,
    LlmProfileCreate,
    LlmProfileOut,
    LlmProfileUpdate,
    MidsceneProfileCreate,
    MidsceneProfileOut,
    MidsceneProfileUpdate,
)
from app.services import settings_crypto
from app.services.ai.effective_ai_settings import invalidate_runtime_cache

router = APIRouter(prefix="/admin/ai-settings", tags=["admin"])

_DIM_WARNING = "修改或启用不同 Embedding 维度可能影响已有 Milvus 向量索引，请谨慎操作。"


def _need_encrypt(val: str) -> str:
    try:
        return settings_crypto.encrypt_secret(val)
    except ValueError as e:
        raise AppError(
            "bad_request",
            "无法保存 API Key：请在环境变量中设置有效的 SETTINGS_ENCRYPTION_KEY（Fernet 密钥）",
            status_code=400,
        ) from e


def _opt_str(v: object | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _apply_api_key(row: SystemLlmProfile | SystemEmbeddingProfile | SystemMidsceneProfile, raw: object | None) -> None:
    if raw is None or raw == "":
        row.api_key_enc = None
    else:
        row.api_key_enc = _need_encrypt(str(raw))


def _llm_out(row: SystemLlmProfile) -> LlmProfileOut:
    key = settings_crypto.decrypt_secret_optional(row.api_key_enc) or ""
    return LlmProfileOut(
        id=row.id,
        name=row.name,
        api_base=(row.api_base or "").strip() or settings.llm_api_base,
        model=(row.model or "").strip() or settings.llm_model,
        allowed_models=(row.allowed_models or "").strip() or settings.llm_allowed_models,
        api_key=key,
        has_api_key=bool(key.strip()),
        is_active=bool(row.is_active),
    )


def _emb_out(row: SystemEmbeddingProfile, *, warning: str | None = None) -> EmbeddingProfileOut:
    key = settings_crypto.decrypt_secret_optional(row.api_key_enc) or ""
    dim = row.dim if row.dim is not None else int(settings.embedding_dim)
    return EmbeddingProfileOut(
        id=row.id,
        name=row.name,
        api_base=(row.api_base or "").strip() or settings.embedding_api_base,
        model=(row.model or "").strip() or settings.embedding_model,
        dim=int(dim),
        api_key=key,
        has_api_key=bool(key.strip()),
        is_active=bool(row.is_active),
        warning=warning,
    )


def _ensure_unique_llm_name(db: Session, name: str, *, exclude_id: int | None = None) -> None:
    q = select(SystemLlmProfile).where(SystemLlmProfile.name == name)
    if exclude_id is not None:
        q = q.where(SystemLlmProfile.id != exclude_id)
    if db.scalar(q.limit(1)) is not None:
        raise AppError("conflict", f"LLM 配置名称已存在：{name}", status_code=409)


def _ensure_unique_emb_name(db: Session, name: str, *, exclude_id: int | None = None) -> None:
    q = select(SystemEmbeddingProfile).where(SystemEmbeddingProfile.name == name)
    if exclude_id is not None:
        q = q.where(SystemEmbeddingProfile.id != exclude_id)
    if db.scalar(q.limit(1)) is not None:
        raise AppError("conflict", f"Embedding 配置名称已存在：{name}", status_code=409)


def _deactivate_all_llm(db: Session) -> None:
    db.execute(update(SystemLlmProfile).values(is_active=False))


def _deactivate_all_emb(db: Session) -> None:
    db.execute(update(SystemEmbeddingProfile).values(is_active=False))


def _has_active_llm(db: Session) -> bool:
    return db.scalar(select(SystemLlmProfile.id).where(SystemLlmProfile.is_active.is_(True)).limit(1)) is not None


def _has_active_emb(db: Session) -> bool:
    return (
        db.scalar(select(SystemEmbeddingProfile.id).where(SystemEmbeddingProfile.is_active.is_(True)).limit(1))
        is not None
    )


def _get_llm(db: Session, profile_id: int) -> SystemLlmProfile:
    row = db.get(SystemLlmProfile, profile_id)
    if row is None:
        raise AppError("not_found", "LLM 配置不存在", status_code=404)
    return row


def _get_emb(db: Session, profile_id: int) -> SystemEmbeddingProfile:
    row = db.get(SystemEmbeddingProfile, profile_id)
    if row is None:
        raise AppError("not_found", "Embedding 配置不存在", status_code=404)
    return row


# ---------- LLM profiles ----------


@router.get("/llm-profiles", response_model=list[LlmProfileOut])
def list_llm_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> list[LlmProfileOut]:
    rows = db.scalars(select(SystemLlmProfile).order_by(SystemLlmProfile.id.asc())).all()
    return [_llm_out(r) for r in rows]


@router.post("/llm-profiles", response_model=LlmProfileOut)
def create_llm_profile(
    body: LlmProfileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> LlmProfileOut:
    name = body.name.strip()
    _ensure_unique_llm_name(db, name)
    activate = body.activate or not _has_active_llm(db)
    if activate:
        _deactivate_all_llm(db)
    row = SystemLlmProfile(
        name=name,
        api_base=_opt_str(body.api_base),
        model=_opt_str(body.model),
        allowed_models=_opt_str(body.allowed_models),
        is_active=activate,
    )
    if body.api_key is not None:
        _apply_api_key(row, body.api_key)
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _llm_out(row)


@router.put("/llm-profiles/{profile_id}", response_model=LlmProfileOut)
def update_llm_profile(
    profile_id: int,
    body: LlmProfileUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> LlmProfileOut:
    row = _get_llm(db, profile_id)
    payload = body.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        _ensure_unique_llm_name(db, name, exclude_id=row.id)
        row.name = name
    if "api_base" in payload:
        row.api_base = _opt_str(payload["api_base"])
    if "model" in payload:
        row.model = _opt_str(payload["model"])
    if "allowed_models" in payload:
        row.allowed_models = _opt_str(payload["allowed_models"])
    if "api_key" in payload:
        _apply_api_key(row, payload["api_key"])
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _llm_out(row)


@router.delete("/llm-profiles/{profile_id}", status_code=204)
def delete_llm_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> None:
    row = _get_llm(db, profile_id)
    if row.is_active:
        raise AppError("bad_request", "不能删除当前启用的 LLM 配置，请先切换启用其它配置", status_code=400)
    db.delete(row)
    db.commit()
    invalidate_runtime_cache()


@router.post("/llm-profiles/{profile_id}/activate", response_model=LlmProfileOut)
def activate_llm_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> LlmProfileOut:
    row = _get_llm(db, profile_id)
    _deactivate_all_llm(db)
    row.is_active = True
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _llm_out(row)


# ---------- Embedding profiles ----------


@router.get("/embedding-profiles", response_model=list[EmbeddingProfileOut])
def list_embedding_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> list[EmbeddingProfileOut]:
    rows = db.scalars(select(SystemEmbeddingProfile).order_by(SystemEmbeddingProfile.id.asc())).all()
    return [_emb_out(r) for r in rows]


@router.post("/embedding-profiles", response_model=EmbeddingProfileOut)
def create_embedding_profile(
    body: EmbeddingProfileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> EmbeddingProfileOut:
    name = body.name.strip()
    _ensure_unique_emb_name(db, name)
    activate = body.activate or not _has_active_emb(db)
    if activate:
        _deactivate_all_emb(db)
    row = SystemEmbeddingProfile(
        name=name,
        api_base=_opt_str(body.api_base),
        model=_opt_str(body.model),
        dim=body.dim,
        is_active=activate,
    )
    if body.api_key is not None:
        _apply_api_key(row, body.api_key)
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    warning = _DIM_WARNING if body.dim is not None else None
    return _emb_out(row, warning=warning)


@router.put("/embedding-profiles/{profile_id}", response_model=EmbeddingProfileOut)
def update_embedding_profile(
    profile_id: int,
    body: EmbeddingProfileUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> EmbeddingProfileOut:
    row = _get_emb(db, profile_id)
    payload = body.model_dump(exclude_unset=True)
    dim_changed = False
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        _ensure_unique_emb_name(db, name, exclude_id=row.id)
        row.name = name
    if "api_base" in payload:
        row.api_base = _opt_str(payload["api_base"])
    if "model" in payload:
        row.model = _opt_str(payload["model"])
    if "dim" in payload:
        if payload["dim"] != row.dim:
            dim_changed = True
        row.dim = payload["dim"]
    if "api_key" in payload:
        _apply_api_key(row, payload["api_key"])
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _emb_out(row, warning=_DIM_WARNING if dim_changed else None)


@router.delete("/embedding-profiles/{profile_id}", status_code=204)
def delete_embedding_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> None:
    row = _get_emb(db, profile_id)
    if row.is_active:
        raise AppError("bad_request", "不能删除当前启用的 Embedding 配置，请先切换启用其它配置", status_code=400)
    db.delete(row)
    db.commit()
    invalidate_runtime_cache()


@router.post("/embedding-profiles/{profile_id}/activate", response_model=EmbeddingProfileOut)
def activate_embedding_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> EmbeddingProfileOut:
    row = _get_emb(db, profile_id)
    _deactivate_all_emb(db)
    row.is_active = True
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _emb_out(row, warning=_DIM_WARNING)


# ---------- Midscene profiles ----------


def _midscene_out(row: SystemMidsceneProfile) -> MidsceneProfileOut:
    key = settings_crypto.decrypt_secret_optional(row.api_key_enc) or ""
    return MidsceneProfileOut(
        id=row.id,
        name=row.name,
        api_base=(row.api_base or "").strip() or settings.midscene_model_base_url,
        model=(row.model or "").strip() or settings.midscene_model_name,
        model_family=(row.model_family or "").strip() or settings.midscene_model_family,
        api_key=key,
        has_api_key=bool(key.strip()),
        is_active=bool(row.is_active),
    )


def _ensure_unique_midscene_name(db: Session, name: str, *, exclude_id: int | None = None) -> None:
    q = select(SystemMidsceneProfile).where(SystemMidsceneProfile.name == name)
    if exclude_id is not None:
        q = q.where(SystemMidsceneProfile.id != exclude_id)
    if db.scalar(q.limit(1)) is not None:
        raise AppError("conflict", f"Midscene 配置名称已存在：{name}", status_code=409)


def _deactivate_all_midscene(db: Session) -> None:
    db.execute(update(SystemMidsceneProfile).values(is_active=False))


def _has_active_midscene(db: Session) -> bool:
    return (
        db.scalar(select(SystemMidsceneProfile.id).where(SystemMidsceneProfile.is_active.is_(True)).limit(1))
        is not None
    )


def _get_midscene(db: Session, profile_id: int) -> SystemMidsceneProfile:
    row = db.get(SystemMidsceneProfile, profile_id)
    if row is None:
        raise AppError("not_found", "Midscene 配置不存在", status_code=404)
    return row


@router.get("/midscene-profiles", response_model=list[MidsceneProfileOut])
def list_midscene_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> list[MidsceneProfileOut]:
    rows = db.scalars(select(SystemMidsceneProfile).order_by(SystemMidsceneProfile.id.asc())).all()
    return [_midscene_out(r) for r in rows]


@router.post("/midscene-profiles", response_model=MidsceneProfileOut)
def create_midscene_profile(
    body: MidsceneProfileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> MidsceneProfileOut:
    name = body.name.strip()
    _ensure_unique_midscene_name(db, name)
    activate = body.activate or not _has_active_midscene(db)
    if activate:
        _deactivate_all_midscene(db)
    row = SystemMidsceneProfile(
        name=name,
        api_base=_opt_str(body.api_base),
        model=_opt_str(body.model),
        model_family=_opt_str(body.model_family),
        is_active=activate,
    )
    if body.api_key is not None:
        _apply_api_key(row, body.api_key)
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _midscene_out(row)


@router.put("/midscene-profiles/{profile_id}", response_model=MidsceneProfileOut)
def update_midscene_profile(
    profile_id: int,
    body: MidsceneProfileUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> MidsceneProfileOut:
    row = _get_midscene(db, profile_id)
    payload = body.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        _ensure_unique_midscene_name(db, name, exclude_id=row.id)
        row.name = name
    if "api_base" in payload:
        row.api_base = _opt_str(payload["api_base"])
    if "model" in payload:
        row.model = _opt_str(payload["model"])
    if "model_family" in payload:
        row.model_family = _opt_str(payload["model_family"])
    if "api_key" in payload:
        _apply_api_key(row, payload["api_key"])
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _midscene_out(row)


@router.delete("/midscene-profiles/{profile_id}", status_code=204)
def delete_midscene_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> None:
    row = _get_midscene(db, profile_id)
    if row.is_active:
        raise AppError("bad_request", "不能删除当前启用的 Midscene 配置，请先切换启用其它配置", status_code=400)
    db.delete(row)
    db.commit()
    invalidate_runtime_cache()


@router.post("/midscene-profiles/{profile_id}/activate", response_model=MidsceneProfileOut)
def activate_midscene_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_superuser),
) -> MidsceneProfileOut:
    row = _get_midscene(db, profile_id)
    _deactivate_all_midscene(db)
    row.is_active = True
    db.commit()
    db.refresh(row)
    invalidate_runtime_cache()
    return _midscene_out(row)
