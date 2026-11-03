"""Merge active LLM/Embedding profiles with app.config.settings (.env fallback)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ai.system_ai_settings import SystemEmbeddingProfile, SystemLlmProfile, SystemMidsceneProfile
from app.services import settings_crypto

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 5.0
_cache_mono = 0.0
_cached_llm: "LlmRuntime | None" = None
_cached_emb: "EmbeddingRuntime | None" = None
_cached_midscene: "MidsceneRuntime | None" = None


@dataclass(frozen=True)
class LlmRuntime:
    api_base: str
    api_key: str
    model: str
    allowed_models_csv: str


@dataclass(frozen=True)
class EmbeddingRuntime:
    api_base: str
    api_key: str
    model: str
    dim: int


@dataclass(frozen=True)
class MidsceneRuntime:
    api_base: str
    api_key: str
    model: str
    model_family: str

    def as_model_config(self) -> dict[str, str]:
        return {
            "MIDSCENE_MODEL_BASE_URL": self.api_base,
            "MIDSCENE_MODEL_API_KEY": self.api_key,
            "MIDSCENE_MODEL_NAME": self.model,
            "MIDSCENE_MODEL_FAMILY": self.model_family,
        }


def invalidate_runtime_cache() -> None:
    global _cache_mono, _cached_llm, _cached_emb, _cached_midscene
    _cache_mono = 0.0
    _cached_llm = None
    _cached_emb = None
    _cached_midscene = None


def _merge_str(db_val: str | None, env_val: str) -> str:
    if db_val is not None and str(db_val).strip():
        return str(db_val).strip()
    return (env_val or "").strip()


@dataclass(frozen=True)
class KeyResolve:
    value: str
    source: str  # db | env | env_fallback_decrypt_failed | none
    db_present: bool
    decrypt_ok: bool


def _key_fingerprint(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return "empty"
    head = s[:6]
    tail = s[-4:] if len(s) >= 4 else s
    return f"len={len(s)} head={head}...tail={tail}"


def _resolve_key(enc: str | None, env_key: str, *, label: str) -> KeyResolve:
    db_present = bool(enc and str(enc).strip())
    if db_present:
        dec = settings_crypto.decrypt_secret_optional(enc)
        if dec:
            log.info("%s API key source=db %s", label, _key_fingerprint(dec))
            return KeyResolve(value=dec, source="db", db_present=True, decrypt_ok=True)
        env_val = (env_key or "").strip()
        log.warning(
            "%s API key: DB 密文存在但解密失败，回退 .env %s",
            label,
            _key_fingerprint(env_val),
        )
        return KeyResolve(
            value=env_val,
            source="env_fallback_decrypt_failed" if env_val else "none",
            db_present=True,
            decrypt_ok=False,
        )
    env_val = (env_key or "").strip()
    if env_val:
        log.info("%s API key source=env %s", label, _key_fingerprint(env_val))
    return KeyResolve(
        value=env_val,
        source="env" if env_val else "none",
        db_present=False,
        decrypt_ok=False,
    )


def _load_active_llm(db: Session) -> SystemLlmProfile | None:
    return db.scalar(select(SystemLlmProfile).where(SystemLlmProfile.is_active.is_(True)).limit(1))


def _load_active_embedding(db: Session) -> SystemEmbeddingProfile | None:
    return db.scalar(
        select(SystemEmbeddingProfile).where(SystemEmbeddingProfile.is_active.is_(True)).limit(1)
    )


def get_llm_runtime(db: Session) -> LlmRuntime:
    global _cache_mono, _cached_llm
    now = time.monotonic()
    if now - _cache_mono < _CACHE_TTL_SEC and _cached_llm is not None:
        return _cached_llm
    row = _load_active_llm(db)
    if row is None:
        llm_key = _resolve_key(None, settings.llm_api_key, label="LLM")
        out = LlmRuntime(
            api_base=_merge_str(None, settings.llm_api_base),
            api_key=llm_key.value,
            model=_merge_str(None, settings.llm_model),
            allowed_models_csv=_merge_str(None, settings.llm_allowed_models),
        )
    else:
        llm_key = _resolve_key(row.api_key_enc, settings.llm_api_key, label="LLM")
        out = LlmRuntime(
            api_base=_merge_str(row.api_base, settings.llm_api_base),
            api_key=llm_key.value,
            model=_merge_str(row.model, settings.llm_model),
            allowed_models_csv=_merge_str(row.allowed_models, settings.llm_allowed_models),
        )
    _cached_llm = out
    _cache_mono = now
    return out


def get_embedding_runtime(db: Session) -> EmbeddingRuntime:
    global _cache_mono, _cached_emb
    now = time.monotonic()
    if now - _cache_mono < _CACHE_TTL_SEC and _cached_emb is not None:
        return _cached_emb
    row = _load_active_embedding(db)
    dim_env = settings.embedding_dim
    if row is None:
        emb_key = _resolve_key(None, settings.embedding_api_key, label="Embedding")
        out = EmbeddingRuntime(
            api_base=_merge_str(None, settings.embedding_api_base),
            api_key=emb_key.value,
            model=_merge_str(None, settings.embedding_model),
            dim=int(dim_env),
        )
    else:
        dim = row.dim if row.dim is not None else int(dim_env)
        emb_key = _resolve_key(row.api_key_enc, settings.embedding_api_key, label="Embedding")
        out = EmbeddingRuntime(
            api_base=_merge_str(row.api_base, settings.embedding_api_base),
            api_key=emb_key.value,
            model=_merge_str(row.model, settings.embedding_model),
            dim=int(dim),
        )
    _cached_emb = out
    _cache_mono = now
    return out


def _load_active_midscene(db: Session) -> SystemMidsceneProfile | None:
    return db.scalar(select(SystemMidsceneProfile).where(SystemMidsceneProfile.is_active.is_(True)).limit(1))


def get_midscene_runtime(db: Session) -> MidsceneRuntime:
    global _cache_mono, _cached_midscene
    now = time.monotonic()
    if now - _cache_mono < _CACHE_TTL_SEC and _cached_midscene is not None:
        return _cached_midscene
    row = _load_active_midscene(db)
    if row is None:
        key = _resolve_key(None, settings.midscene_model_api_key, label="Midscene")
        out = MidsceneRuntime(
            api_base=_merge_str(None, settings.midscene_model_base_url),
            api_key=key.value,
            model=_merge_str(None, settings.midscene_model_name),
            model_family=_merge_str(None, settings.midscene_model_family),
        )
    else:
        key = _resolve_key(row.api_key_enc, settings.midscene_model_api_key, label="Midscene")
        out = MidsceneRuntime(
            api_base=_merge_str(row.api_base, settings.midscene_model_base_url),
            api_key=key.value,
            model=_merge_str(row.model, settings.midscene_model_name),
            model_family=_merge_str(row.model_family, settings.midscene_model_family),
        )
    if not out.api_key.strip():
        llm = get_llm_runtime(db)
        if llm.api_key.strip():
            out = MidsceneRuntime(
                api_base=out.api_base or llm.api_base,
                api_key=llm.api_key,
                model=out.model or llm.model,
                model_family=out.model_family or "qwen2.5-vl",
            )
    _cached_midscene = out
    _cache_mono = now
    return out


def effective_llm_has_api_key(db: Session) -> bool:
    return bool(get_llm_runtime(db).api_key.strip())


def effective_embedding_has_api_key(db: Session) -> bool:
    return bool(get_embedding_runtime(db).api_key.strip())


def describe_key_resolution(db: Session) -> dict[str, str]:
    """Masked diagnostics for logs/support (no full secrets)."""
    llm_row = _load_active_llm(db)
    emb_row = _load_active_embedding(db)
    llm_enc = llm_row.api_key_enc if llm_row else None
    emb_enc = emb_row.api_key_enc if emb_row else None
    llm = _resolve_key(llm_enc, settings.llm_api_key, label="LLM")
    emb = _resolve_key(emb_enc, settings.embedding_api_key, label="Embedding")
    return {
        "llm_source": llm.source,
        "llm_fingerprint": _key_fingerprint(llm.value),
        "embedding_source": emb.source,
        "embedding_fingerprint": _key_fingerprint(emb.value),
        "llm_embedding_same": str(llm.value.strip() == emb.value.strip() and bool(llm.value.strip())),
    }
