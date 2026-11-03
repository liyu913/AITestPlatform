"""Extract plain text and embedded images from uploaded requirement documents."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader

MAX_RAW_BYTES = 512 * 1024
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MIN_IMAGE_BYTES = 2 * 1024
MAX_IMAGE_BYTES = int(1.2 * 1024 * 1024)
MAX_DOCUMENT_IMAGES = 8
MAX_REQUIREMENT_IMAGES = 12

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_TEXT_IMPORT = frozenset({".md", ".txt"})
_TEXT_ATTACH = frozenset({".md", ".txt", ".log", ".json"})
_DOC_ATTACH = frozenset({".pdf", ".docx"})
_IMAGE_ATTACH = frozenset(_IMAGE_MIME.keys())
_ARCHIVE_ATTACH = frozenset({".zip"})
_REQUIREMENT_ATTACH = _TEXT_ATTACH | _DOC_ATTACH | _IMAGE_ATTACH | _ARCHIVE_ATTACH
_ALLOWED = _TEXT_IMPORT | _DOC_ATTACH

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".zip": "application/zip",
    **_IMAGE_MIME,
}


def normalize_extracted(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(t) > MAX_TEXT_CHARS:
        t = t[:MAX_TEXT_CHARS]
    return t


def _extract_pdf(data: bytes, *, allow_empty: bool) -> str:
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as e:  # noqa: BLE001 — user upload
        raise ValueError(f"Invalid or unreadable PDF: {e}") from e
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    text = normalize_extracted("\n\n".join(parts))
    if not text and not allow_empty:
        raise ValueError(
            "No extractable text from PDF (scanned images or empty pages are not supported)"
        )
    return text


def _extract_docx(data: bytes, *, allow_empty: bool) -> str:
    try:
        doc = Document(BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Invalid or unreadable Word document: {e}") from e
    lines = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    text = normalize_extracted("\n".join(lines))
    if not text and not allow_empty:
        raise ValueError("No text content in Word document")
    return text


def _decode_utf8_text(data: bytes, *, label: str) -> str:
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"File must be UTF-8 encoded for {label}") from e
    return normalize_extracted(raw)


def extract_text_from_bytes(filename: str, data: bytes) -> str:
    """Extract text for .md / .txt import into body_md. Rejects PDF/DOCX."""
    suf = Path(filename or "x.bin").suffix.lower()
    if suf in _DOC_ATTACH:
        raise ValueError(
            "PDF / DOCX should be uploaded as attachments for preview, not imported as body"
        )
    if suf not in _TEXT_IMPORT:
        msg = f"Unsupported file type ({suf or 'none'}). Allowed: .md, .txt"
        raise ValueError(msg)
    if len(data) > MAX_RAW_BYTES:
        raise ValueError(f"File too large (max {MAX_RAW_BYTES // 1024} KB)")

    text = _decode_utf8_text(data, label=".md / .txt")
    if not text:
        raise ValueError("No text content in file")
    return text


def extract_attachment_text(filename: str, data: bytes) -> tuple[str, str, str]:
    """Validate PDF/DOCX attachment and extract text (may be empty for scanned PDF).

    Returns (suffix, mime, extracted_text).
    """
    suf = Path(filename or "x.bin").suffix.lower()
    if suf not in _DOC_ATTACH:
        raise ValueError(f"Unsupported attachment type ({suf or 'none'}). Allowed: .pdf, .docx")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)")

    if suf == ".pdf":
        text = _extract_pdf(data, allow_empty=True)
    else:
        text = _extract_docx(data, allow_empty=True)
    return suf, MIME_BY_SUFFIX[suf], text


def requirement_attachment_allowed_suffixes() -> frozenset[str]:
    return _REQUIREMENT_ATTACH


def mime_for_requirement_attachment(filename: str, fallback: str | None = None) -> str:
    suf = Path(filename or "").suffix.lower()
    if suf in MIME_BY_SUFFIX:
        return MIME_BY_SUFFIX[suf]
    return (fallback or "application/octet-stream").strip() or "application/octet-stream"


def extract_requirement_attachment_text(filename: str, data: bytes) -> str:
    """Extract text from a requirement attachment when possible; empty for images/zip."""
    suf = Path(filename or "x.bin").suffix.lower()
    if suf not in _REQUIREMENT_ATTACH:
        allowed = ", ".join(sorted(_REQUIREMENT_ATTACH))
        raise ValueError(f"Unsupported attachment type ({suf or 'none'}). Allowed: {allowed}")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)")
    if suf in _IMAGE_ATTACH or suf in _ARCHIVE_ATTACH:
        return ""
    if suf in _TEXT_ATTACH:
        return _decode_utf8_text(data, label=suf)
    if suf == ".pdf":
        return _extract_pdf(data, allow_empty=True)
    return _extract_docx(data, allow_empty=True)


def _image_mime(name: str, blob: bytes) -> str | None:
    mime = _IMAGE_MIME.get(Path(name).suffix.lower())
    if mime:
        return mime
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if blob.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if blob.startswith(b"RIFF") and blob[8:12] == b"WEBP":
        return "image/webp"
    return None


def _keep_image(blob: bytes) -> bool:
    return MIN_IMAGE_BYTES <= len(blob) <= MAX_IMAGE_BYTES


def _images_from_docx(data: bytes) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = sorted(n for n in zf.namelist() if n.startswith("word/media/"))
            for name in names:
                blob = zf.read(name)
                mime = _image_mime(name, blob)
                if mime is None or not _keep_image(blob):
                    continue
                out.append((mime, blob))
                if len(out) >= MAX_DOCUMENT_IMAGES:
                    break
    except zipfile.BadZipFile:
        return []
    return out


def _images_from_pdf(data: bytes) -> list[tuple[str, bytes]]:
    try:
        reader = PdfReader(BytesIO(data))
    except Exception:  # noqa: BLE001 — user upload
        return []
    out: list[tuple[str, bytes]] = []
    for page in reader.pages:
        images = getattr(page, "images", None) or []
        for img in images:
            blob = getattr(img, "data", None)
            if not isinstance(blob, (bytes, bytearray)):
                continue
            raw = bytes(blob)
            mime = _image_mime(str(getattr(img, "name", "") or ""), raw)
            if mime is None or not _keep_image(raw):
                continue
            out.append((mime, raw))
            if len(out) >= MAX_DOCUMENT_IMAGES:
                return out
    return out


def extract_document_images(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """Return up to 8 embedded images as (mime, bytes) from a PDF or DOCX."""
    suf = Path(filename or "").suffix.lower()
    if suf == ".docx":
        return _images_from_docx(data)
    if suf == ".pdf":
        return _images_from_pdf(data)
    return []


def extract_standalone_image(filename: str, data: bytes) -> tuple[str, bytes] | None:
    """Return (mime, bytes) for a standalone image attachment, or None if not usable."""
    suf = Path(filename or "").suffix.lower()
    if suf not in _IMAGE_ATTACH:
        return None
    mime = _image_mime(filename, data)
    if mime is None:
        return None
    # Standalone uploads: allow smaller icons than embedded doc images.
    if len(data) < 64 or len(data) > MAX_IMAGE_BYTES:
        return None
    return mime, data


def extract_knowledge_text(filename: str, data: bytes) -> str:
    """Extract plain text for knowledge-base indexing (.md / .txt / .pdf / .docx)."""
    suf = Path(filename or "x.bin").suffix.lower()
    if suf in _TEXT_IMPORT:
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)")
        text = _decode_utf8_text(data, label=".md / .txt")
        if not text:
            raise ValueError("No text content in file")
        return text
    if suf in _DOC_ATTACH:
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)")
        if suf == ".pdf":
            return _extract_pdf(data, allow_empty=False)
        return _extract_docx(data, allow_empty=False)
    raise ValueError(f"Unsupported file type ({suf or 'none'}). Allowed: .md, .txt, .pdf, .docx")


def knowledge_allowed_suffixes() -> frozenset[str]:
    return _ALLOWED
