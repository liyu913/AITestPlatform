from __future__ import annotations

import hashlib
import logging

import app.deps as deps_mod
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.errors import AppError
from app.models.knowledge.index_chunk import IndexChunk
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.functional.requirement import Requirement
from app.models.functional.testcase import TestCase
from app.services.knowledge import chunking
from app.services.ai import embedding
from app.services.ai.call_context import project_id_var
from app.services.knowledge import milvus_store
from app.services.ai.effective_ai_settings import get_embedding_runtime
from app.services.file_storage import absolute_path_for_storage_key
from app.services.functional.requirement_content import requirement_text_for_ai
from app.services.knowledge.document_extract import extract_knowledge_text

log = logging.getLogger(__name__)

_INDEX_ERROR_MAX = 500


def format_index_failure(exc: BaseException) -> str:
    if isinstance(exc, AppError) and exc.message.strip():
        msg = exc.message.strip()
    else:
        raw = str(exc).strip() or type(exc).__name__
        if "system_ai_settings" in raw and "doesn't exist" in raw:
            msg = "系统 AI 配置表已升级，请停掉旧的 Celery Worker 后重启再建索引"
        else:
            msg = raw
    if len(msg) > _INDEX_ERROR_MAX:
        msg = msg[:_INDEX_ERROR_MAX] + "..."
    if msg.startswith("索引失败"):
        return msg
    return f"索引失败：{msg}"


def build_text_for_source(db: Session, project_id: int, source_type: str, source_id: int) -> str:
    if source_type == "requirement":
        r = (
            db.query(Requirement)
            .filter(Requirement.id == source_id, Requirement.project_id == project_id)
            .first()
        )
        if r is None:
            return ""
        return f"{r.title}\n{requirement_text_for_ai(r)}"
    if source_type == "case":
        c = (
            db.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id == source_id, TestCase.project_id == project_id)
            .first()
        )
        if c is None:
            return ""
        lines = [c.title]
        case_type = (c.case_type or "").strip()
        if case_type:
            lines.append(f"类型: {case_type}")
        precondition = (c.precondition or "").strip()
        if precondition:
            lines.append(f"前置条件: {precondition}")
        from app.services.functional.test_data_vars import case_test_data_text

        test_data = case_test_data_text(variables=c.variables_json, test_data=c.test_data)
        if test_data:
            lines.append(f"测试数据: {test_data}")
        for s in sorted(c.steps, key=lambda x: x.order):
            lines.append(f"{s.order}. {s.action} -> {s.expected}")
        overall = (c.overall_expected or "").strip()
        if overall:
            lines.append(f"总体期望: {overall}")
        return "\n".join(lines)
    if source_type == "upload":
        d = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.id == source_id, KnowledgeDocument.project_id == project_id)
            .first()
        )
        if d is None:
            return ""
        path = absolute_path_for_storage_key(d.storage_path)
        try:
            data = path.read_bytes()
            return extract_knowledge_text(d.filename, data)
        except (OSError, ValueError):
            return ""
    return ""


def run_reindex(project_id: int, source_type: str, source_id: int) -> None:
    ctx_token = project_id_var.set(project_id)
    try:
        _execute_reindex(project_id, source_type, source_id)
    finally:
        project_id_var.reset(ctx_token)


def _execute_reindex(project_id: int, source_type: str, source_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        emb = get_embedding_runtime(db)

        def _mark_upload_error(msg: str) -> None:
            if source_type != "upload":
                return
            doc = (
                db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.id == source_id,
                    KnowledgeDocument.project_id == project_id,
                )
                .first()
            )
            if doc is None:
                return
            doc.error_message = msg
            db.commit()

        if not emb.api_key.strip():
            log.warning("skip reindex: embedding_api_key empty")
            _mark_upload_error("索引跳过：未配置 Embedding API Key")
            return

        existing = (
            db.query(IndexChunk)
            .filter(
                IndexChunk.project_id == project_id,
                IndexChunk.source_type == source_type,
                IndexChunk.source_id == source_id,
            )
            .all()
        )
        old_ids = [str(c.id) for c in existing]
        if old_ids:
            try:
                coll = milvus_store.ensure_collection(emb.dim)
                milvus_store.delete_by_chunk_ids(coll, old_ids)
            except AppError:
                raise
            except Exception:
                log.exception("milvus delete skipped or failed")
        for c in existing:
            db.delete(c)
        db.commit()

        text = build_text_for_source(db, project_id, source_type, source_id)
        if not text.strip():
            _mark_upload_error("索引失败：未能从文档提取文本")
            return

        mode = chunking.CHUNK_MODE_WINDOW
        window = settings.chunk_window
        overlap = settings.chunk_overlap
        if source_type == "upload":
            doc_row = (
                db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.id == source_id,
                    KnowledgeDocument.project_id == project_id,
                )
                .first()
            )
            if doc_row is not None:
                lib = db.get(KnowledgeLibrary, doc_row.library_id)
                if lib is not None:
                    mode = lib.chunk_mode or chunking.CHUNK_MODE_WINDOW
                    window = int(lib.chunk_window or settings.chunk_window)
                    overlap = int(lib.chunk_overlap if lib.chunk_overlap is not None else settings.chunk_overlap)
        if overlap >= window:
            overlap = max(0, window // 6)
        pieces = chunking.chunk_document(text, mode=mode, window=window, overlap=overlap)
        for i, piece in enumerate(pieces):
            h = hashlib.sha256(piece.encode("utf-8")).hexdigest()
            db.add(
                IndexChunk(
                    project_id=project_id,
                    source_type=source_type,
                    source_id=source_id,
                    chunk_index=i,
                    content_hash=h,
                    text=piece,
                    embedding_model=emb.model,
                    model_version="1",
                    index_status="pending",
                )
            )
        db.flush()

        chunks = (
            db.query(IndexChunk)
            .filter(
                IndexChunk.project_id == project_id,
                IndexChunk.source_type == source_type,
                IndexChunk.source_id == source_id,
            )
            .order_by(IndexChunk.chunk_index)
            .all()
        )
        texts = [c.text for c in chunks]
        all_emb: list[list[float]] = []
        batch_size = 16
        for off in range(0, len(texts), batch_size):
            all_emb.extend(
                embedding.embed_texts(texts[off : off + batch_size], db, scene="知识库向量化")
            )

        coll = milvus_store.ensure_collection(emb.dim)
        chunk_ids = [str(c.id) for c in chunks]
        project_ids = [project_id] * len(chunks)
        source_types = [source_type] * len(chunks)
        source_ids = [source_id] * len(chunks)
        previews = [milvus_store.truncate_text_preview(c.text) for c in chunks]
        milvus_store.upsert_vectors(
            coll,
            chunk_ids=chunk_ids,
            project_ids=project_ids,
            source_types=source_types,
            source_ids=source_ids,
            text_previews=previews,
            embeddings=all_emb,
        )
        for c in chunks:
            c.milvus_primary_key = str(c.id)
            c.index_status = "ok"
        if source_type == "upload":
            doc = (
                db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.id == source_id,
                    KnowledgeDocument.project_id == project_id,
                )
                .first()
            )
            if doc is not None:
                doc.error_message = None
        db.commit()
    except Exception as exc:
        db.rollback()
        log.exception("run_reindex failed for %s/%s/%s", project_id, source_type, source_id)
        try:
            if source_type == "upload":
                doc = (
                    db.query(KnowledgeDocument)
                    .filter(
                        KnowledgeDocument.id == source_id,
                        KnowledgeDocument.project_id == project_id,
                    )
                    .first()
                )
                if doc is not None:
                    doc.error_message = format_index_failure(exc)
                    db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()
