"""Text chunking: fixed window or heading/paragraph split (Unicode-safe)."""

from __future__ import annotations

import re

CHUNK_MODE_WINDOW = "window"
CHUNK_MODE_HEADING = "heading"
CHUNK_MODES = frozenset({CHUNK_MODE_WINDOW, CHUNK_MODE_HEADING})

_HEADING_SPLIT = re.compile(r"(?m)(?=^#{1,6}[ \t])")
_PARA_SPLIT = re.compile(r"\n\s*\n")


def chunk_text(text: str, window: int, overlap: int) -> list[str]:
    if window <= 0:
        raise ValueError("window must be positive")
    if overlap < 0 or overlap >= window:
        raise ValueError("overlap must be in [0, window)")
    if not text:
        return []

    step = window - overlap
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        chunk = text[start : start + window]
        chunks.append(chunk)
        if start + window >= n:
            break
        start += step
    return chunks


def _split_heading_sections(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = [p.strip() for p in _HEADING_SPLIT.split(normalized) if p.strip()]
    if len(parts) >= 2:
        return parts
    paras = [p.strip() for p in _PARA_SPLIT.split(normalized) if p.strip()]
    if paras:
        return paras
    return [normalized.strip()] if normalized.strip() else []


def chunk_by_heading(text: str, window: int, overlap: int) -> list[str]:
    """Split on Markdown headings (or blank-line paragraphs), then window oversized sections."""
    out: list[str] = []
    for section in _split_heading_sections(text):
        if len(section) <= window:
            out.append(section)
        else:
            out.extend(chunk_text(section, window, overlap))
    return out


def chunk_document(text: str, *, mode: str, window: int, overlap: int) -> list[str]:
    resolved = (mode or CHUNK_MODE_WINDOW).strip().lower()
    if resolved == CHUNK_MODE_HEADING:
        return chunk_by_heading(text, window, overlap)
    return chunk_text(text, window, overlap)
