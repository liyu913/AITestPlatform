"""Resolve requirement text / images for search / indexing / AI."""

from __future__ import annotations

from pathlib import Path

from app.models.functional.requirement import Requirement
from app.services.file_storage import absolute_path_for_storage_key
from app.services.knowledge.document_extract import (
    MAX_REQUIREMENT_IMAGES,
    extract_document_images,
    extract_requirement_attachment_text,
    extract_standalone_image,
)

_SCAN_PLACEHOLDER_PREFIX = "（原文件"
_SCAN_PLACEHOLDER_HINT = "暂无可用文本"


def _attachment_names(r: Requirement) -> list[str]:
    atts = list(getattr(r, "attachments", None) or [])
    return [a.filename for a in atts if a.filename]


def requirement_text_for_ai(r: Requirement) -> str:
    body = (r.body_md or "").strip()
    if body:
        return body
    extracted = (r.extracted_text or "").strip()
    if extracted:
        return extracted
    names = _attachment_names(r)
    if names:
        joined = "、".join(names[:5])
        more = f" 等 {len(names)} 个" if len(names) > 5 else ""
        return f"（原文件 {joined}{more} 暂无可用文本，可能为扫描件或图片）"
    return ""


def usable_requirement_text(raw: str, *, max_chars: int | None = None) -> str:
    """Drop scan placeholders; optionally truncate for LLM prompts."""
    text = (raw or "").strip()
    if text.startswith(_SCAN_PLACEHOLDER_PREFIX) and _SCAN_PLACEHOLDER_HINT in text:
        return ""
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars]
    return text


def recompute_extracted_text(r: Requirement) -> None:
    """Refresh requirement.extracted_text from attachment files on disk."""
    parts: list[str] = []
    for att in list(getattr(r, "attachments", None) or []):
        path = absolute_path_for_storage_key(att.storage_key)
        if not path.is_file():
            continue
        try:
            text = extract_requirement_attachment_text(att.filename, path.read_bytes())
        except ValueError:
            continue
        text = (text or "").strip()
        if text:
            parts.append(f"## {att.filename}\n{text}")
    joined = "\n\n".join(parts).strip()
    r.extracted_text = joined or None


def requirement_images_for_ai(r: Requirement) -> list[tuple[str, bytes]]:
    """Collect standalone image attachments + embedded PDF/DOCX images (capped)."""
    out: list[tuple[str, bytes]] = []
    for att in list(getattr(r, "attachments", None) or []):
        if len(out) >= MAX_REQUIREMENT_IMAGES:
            break
        path = absolute_path_for_storage_key(att.storage_key)
        if not path.is_file():
            continue
        data = path.read_bytes()
        suf = Path(att.filename or "").suffix.lower()
        if suf in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            standalone = extract_standalone_image(att.filename, data)
            if standalone is not None:
                out.append(standalone)
            continue
        for mime, blob in extract_document_images(att.filename, data):
            out.append((mime, blob))
            if len(out) >= MAX_REQUIREMENT_IMAGES:
                break
    return out[:MAX_REQUIREMENT_IMAGES]


def requirement_assets_for_ai(r: Requirement) -> tuple[str, list[tuple[str, bytes]]]:
    """Return (usable_text, images) for multimodal AI flows."""
    text = usable_requirement_text(requirement_text_for_ai(r))
    images = requirement_images_for_ai(r)
    return text, images
