import pytest

from app.config import settings
from app.errors import AppError
from app.models.knowledge.index_chunk import IndexChunk
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.project.project import Project
from app.models.identity.user import User
from app.services.knowledge import indexing
from app.services.knowledge.indexing import format_index_failure
from tests.conftest import TestingSessionLocal


def test_format_index_failure_app_error():
    msg = format_index_failure(AppError("bad_request", "Embedding API Key 无效或未授权"))
    assert msg == "索引失败：Embedding API Key 无效或未授权"


def test_format_index_failure_plain():
    assert "connection refused" in format_index_failure(ConnectionError("connection refused"))


def test_format_index_failure_legacy_settings_table():
    err = Exception("(1146, \"Table 'raina.system_ai_settings' doesn't exist\")")
    assert "停掉旧的 Celery Worker" in format_index_failure(err)


def _seed_upload_doc() -> tuple[int, int]:
    db = TestingSessionLocal()
    try:
        u = User(username="idx_err_u", password_hash="x", is_active=True)
        db.add(u)
        db.flush()
        p = Project(name="idx", description="", owner_id=u.id)
        db.add(p)
        db.flush()
        lib = KnowledgeLibrary(project_id=p.id, name="lib")
        db.add(lib)
        db.flush()
        doc = KnowledgeDocument(
            project_id=p.id,
            library_id=lib.id,
            filename="a.md",
            storage_path="1/a.md",
            parse_status="ready",
            size_bytes=12,
        )
        db.add(doc)
        db.flush()
        db.add(
            IndexChunk(
                id=99001,
                project_id=p.id,
                source_type="upload",
                source_id=doc.id,
                chunk_index=0,
                content_hash="x",
                text="t",
                embedding_model="m",
                model_version="1",
                index_status="ok",
            )
        )
        db.commit()
        return p.id, doc.id
    finally:
        db.close()


def test_case_index_text_includes_type_and_precondition():
    from app.models.functional.testcase import TestCase, TestCaseStep

    db = TestingSessionLocal()
    try:
        u = User(username="idx_case_u", password_hash="x", is_active=True)
        db.add(u)
        db.flush()
        p = Project(name="idx-case", description="", owner_id=u.id)
        db.add(p)
        db.flush()
        case = TestCase(
            project_id=p.id,
            title="登录失败",
            case_type="异常",
            precondition="账号已注册",
            test_data="账号 demo 密码 wrong",
            overall_expected="提示密码错误",
        )
        db.add(case)
        db.flush()
        db.add(TestCaseStep(case_id=case.id, order=0, action="输入错误密码", expected="—"))
        db.commit()
        text = indexing.build_text_for_source(db, p.id, "case", case.id)
    finally:
        db.close()
    assert "类型: 异常" in text
    assert "前置条件: 账号已注册" in text
    assert "测试数据: 账号 demo 密码 wrong" in text
    assert "总体期望: 提示密码错误" in text


def test_run_reindex_persists_real_error(monkeypatch):
    import app.deps as deps_mod

    monkeypatch.setattr(deps_mod, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(settings, "embedding_api_key", "sk-test")

    def _boom(_dim):
        raise AppError(
            "bad_request",
            "Embedding API Key 无效或未授权，请检查 DashScope 密钥配置",
            status_code=400,
        )

    monkeypatch.setattr(indexing.milvus_store, "ensure_collection", _boom)

    pid, doc_id = _seed_upload_doc()
    with pytest.raises(AppError):
        indexing.run_reindex(pid, "upload", doc_id)

    db = TestingSessionLocal()
    try:
        doc = db.get(KnowledgeDocument, doc_id)
        assert doc is not None
        assert "Embedding API Key 无效" in (doc.error_message or "")
    finally:
        db.close()
