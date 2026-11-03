"""Graph captcha challenge storage (process-local, TTL)."""

from __future__ import annotations

import base64
import os
import secrets
import threading
import time
import uuid
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import settings

_lock = threading.Lock()
_store: dict[str, tuple[str, float]] = {}

# Avoid look-alikes that confuse vision models: 0/o, 1/l/i, 2/z, 5/s, 6/g, 8/b, 9/q-ish.
_ALPHABET = "acdefghjkmnpqrtuvwxy3467"
_CODE_LEN = 4
# Compact strip: tight glyph packing so CSS height can match the input field.
_WIDTH = 120
_HEIGHT = 40
_FONT_SIZE = 30
_CHAR_GAP = 4

# Match frontend light flat theme
_CAPTCHA_BG = (247, 247, 248)
_CAPTCHA_FG = (28, 28, 28)


def _purge_expired() -> None:
    deadline = time.monotonic()
    dead = [k for k, (_, exp) in _store.items() if exp < deadline]
    for k in dead:
        del _store[k]


@lru_cache(maxsize=1)
def _load_font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        import captcha

        font_path = Path(captcha.__file__).resolve().parent / "data" / "DroidSansMono.ttf"
        if font_path.is_file():
            return ImageFont.truetype(str(font_path), _FONT_SIZE)
    except Exception:
        pass
    return ImageFont.load_default()


def _render_png(code: str) -> bytes:
    """Upright, tightly packed glyphs; no rotation, warp, or crossing noise lines."""
    img = Image.new("RGB", (_WIDTH, _HEIGHT), _CAPTCHA_BG)
    draw = ImageDraw.Draw(img)
    font = _load_font()
    boxes = [draw.textbbox((0, 0), ch, font=font) for ch in code]
    widths = [b[2] - b[0] for b in boxes]
    total = sum(widths) + _CHAR_GAP * (len(code) - 1)
    x = (_WIDTH - total) / 2
    for ch, bbox, tw in zip(code, boxes, widths, strict=True):
        th = bbox[3] - bbox[1]
        y = int((_HEIGHT - th) / 2) - bbox[1]
        draw.text((int(x) - bbox[0], y), ch, font=font, fill=_CAPTCHA_FG)
        x += tw + _CHAR_GAP
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_captcha() -> tuple[str, str]:
    """Return (captcha_id, image_base64_png)."""
    ttl = max(30, settings.captcha_ttl_seconds)
    code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    cid = str(uuid.uuid4())
    b64 = base64.standard_b64encode(_render_png(code)).decode("ascii")

    with _lock:
        _purge_expired()
        _store[cid] = (code.lower(), time.monotonic() + ttl)
    return cid, b64


def verify_and_consume(captcha_id: str | None, user_input: str | None) -> bool:
    if not captcha_id or not user_input or not user_input.strip():
        return False
    key = captcha_id.strip()
    guess = user_input.strip().lower()
    with _lock:
        _purge_expired()
        ent = _store.pop(key, None)
        if ent is None:
            return False
        answer, _exp = ent
        return guess == answer


def peek_answer_pytest_only(captcha_id: str) -> str | None:
    """Only for pytest; returns lowercase answer if entry exists."""
    if not os.getenv("PYTEST_CURRENT_TEST"):
        raise RuntimeError("peek_answer_pytest_only is test-only")
    with _lock:
        ent = _store.get(captcha_id)
        return ent[0] if ent else None
