import io

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.tasks.celery_app import celery_app
from tests.conftest import TestingSessionLocal


@pytest.fixture(autouse=True)
def _celery_eager():
    prev_e, prev_p = celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = prev_e
    celery_app.conf.task_eager_propagates = prev_p


@pytest.fixture(autouse=True)
def _tasks_use_test_db(monkeypatch):
    import app.deps as deps_mod

    monkeypatch.setattr(deps_mod, "SessionLocal", TestingSessionLocal)


def _user(username: str, pw: str):
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "K"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_upload_md_parse_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("ku_x", "k")
    tok = _login("ku_x", "k")
    pid = _project(tok)
    client = TestClient(app)

    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("note.md", io.BytesIO(b"# Hello\nworld"), "text/markdown")},
    )
    assert r.status_code == 201
    row = r.json()
    assert row["parse_status"] == "ready"
    assert row["filename"] == "note.md"

    r2 = client.get(
        f"/api/v1/projects/{pid}/knowledge/",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.status_code == 200
    assert len(r2.json()) == 1


def test_upload_with_display_name(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("ku_name", "k")
    tok = _login("ku_name", "k")
    pid = _project(tok)
    client = TestClient(app)

    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("note.md", io.BytesIO(b"# Hello\nworld"), "text/markdown")},
        data={"name": "登录流程"},
    )
    assert r.status_code == 201
    assert r.json()["filename"] == "登录流程.md"


def test_reindex_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kr_x", "r")
    tok = _login("kr_x", "r")
    pid = _project(tok)
    client = TestClient(app)
    up = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("a.txt", io.BytesIO(b"ok"), "text/plain")},
    )
    doc_id = up.json()["id"]

    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/{doc_id}/reindex",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 202


def test_bad_extension_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kb_x", "b")
    tok = _login("kb_x", "b")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={"file": ("x.xlsx", io.BytesIO(b"x"), "application/vnd.ms-excel")},
    )
    assert r.status_code == 400


def test_upload_docx_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Hello from docx knowledge")
    doc.save(buf)
    buf.seek(0)

    _user("kd_x", "d")
    tok = _login("kd_x", "d")
    pid = _project(tok)
    client = TestClient(app)
    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers={"Authorization": f"Bearer {tok}"},
        files={
            "file": (
                "spec.docx",
                buf,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r.status_code == 201
    assert r.json()["parse_status"] == "ready"
    assert r.json()["filename"] == "spec.docx"


def test_delete_knowledge_document(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kdel", "d")
    tok = _login("kdel", "d")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}
    up = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers=headers,
        files={"file": ("del.md", io.BytesIO(b"# del"), "text/markdown")},
    )
    assert up.status_code == 201
    doc_id = up.json()["id"]

    from app.models.knowledge.index_chunk import IndexChunk

    db = TestingSessionLocal()
    try:
        db.add(
            IndexChunk(
                id=9101,
                project_id=pid,
                source_type="upload",
                source_id=doc_id,
                chunk_index=0,
                content_hash="x",
                text="t",
                embedding_model="m",
                model_version="1",
                index_status="ok",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/v1/projects/{pid}/knowledge/{doc_id}", headers=headers)
    assert r.status_code == 204
    r2 = client.get(f"/api/v1/projects/{pid}/knowledge/", headers=headers)
    assert r2.status_code == 200
    assert all(x["id"] != doc_id for x in r2.json())
    r3 = client.get(f"/api/v1/projects/{pid}/knowledge/chunks", headers=headers)
    assert r3.status_code == 200
    assert all(x["document_id"] != doc_id for x in r3.json())


def test_knowledge_libraries_list_create_delete_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kl_x", "k")
    tok = _login("kl_x", "k")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    r = client.get(f"/api/v1/projects/{pid}/knowledge/libraries", headers=headers)
    assert r.status_code == 200
    libs = r.json()
    assert len(libs) >= 1
    assert any("默认" in x["name"] for x in libs)

    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries",
        headers=headers,
        json={"name": "ExtraLib", "description": "test"},
    )
    assert r.status_code == 201
    extra_id = r.json()["id"]
    assert r.json()["name"] == "ExtraLib"
    assert r.json()["chunk_mode"] == "window"
    assert r.json()["chunk_window"] == 400
    assert r.json()["chunk_overlap"] == 80

    r = client.delete(
        f"/api/v1/projects/{pid}/knowledge/libraries/{extra_id}",
        headers=headers,
    )
    assert r.status_code == 204


def test_knowledge_library_chunk_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kl_chunk", "k")
    tok = _login("kl_chunk", "k")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries",
        headers=headers,
        json={
            "name": "ChunkLib",
            "chunk_mode": "heading",
            "chunk_window": 400,
            "chunk_overlap": 40,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["chunk_mode"] == "heading"
    assert body["chunk_window"] == 400
    assert body["chunk_overlap"] == 40
    lid = body["id"]

    bad = client.patch(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}",
        headers=headers,
        json={"chunk_overlap": 500},
    )
    assert bad.status_code == 400

    ok = client.patch(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}",
        headers=headers,
        json={"chunk_mode": "window", "chunk_window": 600, "chunk_overlap": 80},
    )
    assert ok.status_code == 200
    assert ok.json()["chunk_window"] == 600
    assert ok.json()["chunk_mode"] == "window"
    assert ok.json()["reindex_required"] is False

    ri = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}/reindex",
        headers=headers,
    )
    assert ri.status_code == 202
    assert ri.json()["queued"] == 0


def test_knowledge_library_delete_cascades_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kl2", "k")
    tok = _login("kl2", "k")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    r = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries",
        headers=headers,
        json={"name": "WithDocs"},
    )
    assert r.status_code == 201
    lid = r.json()["id"]

    up = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}/upload",
        headers=headers,
        files={"file": ("n.md", io.BytesIO(b"# x"), "text/markdown")},
    )
    assert up.status_code == 201
    doc_id = up.json()["id"]

    from app.models.knowledge.index_chunk import IndexChunk

    db = TestingSessionLocal()
    try:
        db.add(
            IndexChunk(
                id=9101,
                project_id=pid,
                source_type="upload",
                source_id=doc_id,
                chunk_index=0,
                content_hash="del",
                text="to be purged",
                embedding_model="test-model",
                model_version="1",
                index_status="ok",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/v1/projects/{pid}/knowledge/libraries/{lid}", headers=headers)
    assert r.status_code == 204

    libs = client.get(f"/api/v1/projects/{pid}/knowledge/libraries", headers=headers)
    assert libs.status_code == 200
    assert all(item["id"] != lid for item in libs.json())

    docs = client.get(f"/api/v1/projects/{pid}/knowledge/", headers=headers)
    assert docs.status_code == 200
    assert all(item["id"] != doc_id for item in docs.json())

    chunks = client.get(f"/api/v1/projects/{pid}/knowledge/chunks", headers=headers)
    assert chunks.status_code == 200
    assert all(item["document_id"] != doc_id for item in chunks.json())


def test_library_status_counts_reindex_flag_and_index_status(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(
        "app.api.knowledge.knowledge.effective_embedding_has_api_key",
        lambda _db: False,
    )
    _user("kl_status", "k")
    tok = _login("kl_status", "k")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    created = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries",
        headers=headers,
        json={"name": "StatusLib"},
    )
    assert created.status_code == 201
    lid = created.json()["id"]
    assert created.json()["ready_count"] == 0
    assert created.json()["reindex_required"] is False

    up = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}/upload",
        headers=headers,
        files={"file": ("ready.md", io.BytesIO(b"# ready"), "text/markdown")},
    )
    assert up.status_code == 201
    doc_id = up.json()["id"]

    from app.models.knowledge.knowledge import KnowledgeDocument

    db = TestingSessionLocal()
    try:
        doc = db.get(KnowledgeDocument, doc_id)
        assert doc is not None
        doc.parse_status = "failed"
        db.commit()
    finally:
        db.close()

    listed = client.get(f"/api/v1/projects/{pid}/knowledge/libraries", headers=headers)
    assert listed.status_code == 200
    lib = next(item for item in listed.json() if item["id"] == lid)
    assert lib["document_count"] == 1
    assert lib["failed_count"] == 1
    assert lib["ready_count"] == 0
    assert lib["parsing_count"] == 0
    assert lib["reindex_required"] is False

    renamed = client.patch(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}",
        headers=headers,
        json={"name": "StatusLibRenamed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["reindex_required"] is False

    patched = client.patch(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}",
        headers=headers,
        json={"chunk_window": 500, "chunk_overlap": 40},
    )
    assert patched.status_code == 200
    assert patched.json()["reindex_required"] is True

    db = TestingSessionLocal()
    try:
        doc = db.get(KnowledgeDocument, doc_id)
        assert doc is not None
        doc.parse_status = "pending"
        db.commit()
    finally:
        db.close()

    listed = client.get(f"/api/v1/projects/{pid}/knowledge/libraries", headers=headers)
    lib = next(item for item in listed.json() if item["id"] == lid)
    assert lib["parsing_count"] == 1
    assert lib["failed_count"] == 0

    ri = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries/{lid}/reindex",
        headers=headers,
    )
    assert ri.status_code == 202
    assert ri.json()["queued"] == 1

    listed = client.get(f"/api/v1/projects/{pid}/knowledge/libraries", headers=headers)
    lib = next(item for item in listed.json() if item["id"] == lid)
    assert lib["reindex_required"] is False

    status = client.get(f"/api/v1/projects/{pid}/knowledge/index-status", headers=headers)
    assert status.status_code == 200
    assert status.json() == {"embedding_ready": False}


def test_list_knowledge_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kc_x", "k")
    tok = _login("kc_x", "k")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    up = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers=headers,
        files={"file": ("chunk.md", io.BytesIO(b"# Title\nbody text"), "text/markdown")},
    )
    assert up.status_code == 201
    doc = up.json()
    doc_id = doc["id"]
    library_id = doc["library_id"]

    from app.models.knowledge.index_chunk import IndexChunk

    db = TestingSessionLocal()
    try:
        db.add(
            IndexChunk(
                id=9001,
                project_id=pid,
                source_type="upload",
                source_id=doc_id,
                chunk_index=0,
                content_hash="abc",
                text="chunk zero text",
                embedding_model="test-model",
                model_version="1",
                index_status="ok",
            )
        )
        db.add(
            IndexChunk(
                id=9002,
                project_id=pid,
                source_type="upload",
                source_id=doc_id,
                chunk_index=1,
                content_hash="def",
                text="chunk one text",
                embedding_model="test-model",
                model_version="1",
                index_status="pending",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/v1/projects/{pid}/knowledge/chunks", headers=headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["chunk_index"] == 0
    assert items[0]["text"] == "chunk zero text"
    assert items[0]["document_id"] == doc_id
    assert items[0]["document_filename"] == "chunk.md"
    assert items[0]["library_id"] == library_id

    r = client.get(
        f"/api/v1/projects/{pid}/knowledge/chunks",
        headers=headers,
        params={"document_id": doc_id},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get(
        f"/api/v1/projects/{pid}/knowledge/chunks",
        headers=headers,
        params={"library_id": library_id},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get(
        f"/api/v1/projects/{pid}/knowledge/chunks",
        headers=headers,
        params={"library_id": 999999},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_document_category_upload_patch_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    _user("kc_cat_x", "c")
    tok = _login("kc_cat_x", "c")
    pid = _project(tok)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {tok}"}

    up_none = client.post(
        f"/api/v1/projects/{pid}/knowledge/upload",
        headers=headers,
        files={"file": ("plain.txt", io.BytesIO(b"no category"), "text/plain")},
    )
    assert up_none.status_code == 201
    assert up_none.json()["category"] is None
    none_id = up_none.json()["id"]
    library_id = up_none.json()["library_id"]

    up_prd = client.post(
        f"/api/v1/projects/{pid}/knowledge/libraries/{library_id}/upload",
        headers=headers,
        files={"file": ("prd.md", io.BytesIO(b"# PRD"), "text/markdown")},
        data={"category": "  PRD  "},
    )
    assert up_prd.status_code == 201
    assert up_prd.json()["category"] == "PRD"
    prd_id = up_prd.json()["id"]

    listed = client.get(
        f"/api/v1/projects/{pid}/knowledge/libraries/{library_id}/documents",
        headers=headers,
        params={"category": "PRD"},
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == prd_id

    uncat = client.get(
        f"/api/v1/projects/{pid}/knowledge/libraries/{library_id}/documents",
        headers=headers,
        params={"uncategorized": "true"},
    )
    assert uncat.status_code == 200
    assert {row["id"] for row in uncat.json()} == {none_id}

    patched = client.patch(
        f"/api/v1/projects/{pid}/knowledge/{none_id}",
        headers=headers,
        json={"category": "接口文档"},
    )
    assert patched.status_code == 200
    assert patched.json()["category"] == "接口文档"

    cleared = client.patch(
        f"/api/v1/projects/{pid}/knowledge/{prd_id}",
        headers=headers,
        json={"category": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["category"] is None

    all_uncat = client.get(
        f"/api/v1/projects/{pid}/knowledge/",
        headers=headers,
        params={"uncategorized": "true"},
    )
    assert all_uncat.status_code == 200
    assert {row["id"] for row in all_uncat.json()} == {prd_id}
