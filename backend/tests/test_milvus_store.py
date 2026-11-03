from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.services.knowledge.milvus_store import (
    MILVUS_TEXT_PREVIEW_MAX_BYTES,
    collection_embedding_dim,
    ensure_collection,
    truncate_text_preview,
)


def test_truncate_text_preview_short_unchanged():
    s = "hello"
    assert truncate_text_preview(s) == s


def test_truncate_text_preview_limits_utf8_bytes():
    # 400 CJK chars ≈ 1200 UTF-8 bytes; must fit Milvus VARCHAR limit
    s = "测" * 400
    out = truncate_text_preview(s)
    assert len(out.encode("utf-8")) <= MILVUS_TEXT_PREVIEW_MAX_BYTES
    assert out  # non-empty after truncation


def test_collection_embedding_dim_from_field_dim():
    field = SimpleNamespace(name="embedding", dim=1024, params={})
    coll = SimpleNamespace(schema=SimpleNamespace(fields=[field]))
    assert collection_embedding_dim(coll) == 1024


def test_ensure_collection_rejects_dim_mismatch(monkeypatch):
    field = SimpleNamespace(name="embedding", dim=1024, params={})
    coll = SimpleNamespace(schema=SimpleNamespace(fields=[field]), load=lambda: None)

    monkeypatch.setattr("app.services.knowledge.milvus_store.connect", lambda: None)
    monkeypatch.setattr("app.services.knowledge.milvus_store.utility.has_collection", lambda *_a, **_k: True)
    monkeypatch.setattr("app.services.knowledge.milvus_store.Collection", lambda *_a, **_k: coll)

    with pytest.raises(AppError) as ei:
        ensure_collection(1536)
    assert "1024" in ei.value.message
    assert "1536" in ei.value.message
