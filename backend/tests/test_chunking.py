from app.services.knowledge.chunking import chunk_by_heading, chunk_document, chunk_text


def test_chunk_overlap():
    text = "a" * 1200
    chunks = chunk_text(text, window=400, overlap=50)
    assert len(chunks) >= 3
    assert all(len(c) <= 400 for c in chunks)


def test_chunk_empty():
    assert chunk_text("", window=100, overlap=10) == []


def test_chunk_by_heading_keeps_sections():
    text = "# A\nhello\n\n# B\nworld"
    chunks = chunk_by_heading(text, window=800, overlap=20)
    assert len(chunks) == 2
    assert chunks[0].startswith("# A")
    assert chunks[1].startswith("# B")


def test_chunk_by_heading_windows_oversized_section():
    text = "# A\n" + ("x" * 500)
    chunks = chunk_by_heading(text, window=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_chunk_document_heading_mode():
    text = "# 一\n甲\n\n# 二\n乙"
    out = chunk_document(text, mode="heading", window=800, overlap=40)
    assert len(out) == 2
