from sqlalchemy.orm import Session

from app.errors import AppError
from app.user_messages import KNOWLEDGE_LIBRARY_NOT_IN_PROJECT
from app.models.knowledge.index_chunk import IndexChunk
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.functional.requirement import Requirement
from app.models.functional.testcase import TestCase
from app.schemas.knowledge.rag import RagHitOut
from app.services.ai import embedding
from app.services.knowledge import milvus_store
from app.services.ai.effective_ai_settings import get_embedding_runtime


def upload_document_ids_for_library(db: Session, project_id: int, library_id: int) -> list[int]:
    """IDs of knowledge documents in this library; raises if library missing or wrong project."""
    lib = db.get(KnowledgeLibrary, library_id)
    if lib is None or lib.project_id != project_id:
        raise AppError("bad_request", KNOWLEDGE_LIBRARY_NOT_IN_PROJECT, status_code=400)
    rows = (
        db.query(KnowledgeDocument.id)
        .filter(
            KnowledgeDocument.library_id == library_id,
            KnowledgeDocument.project_id == project_id,
        )
        .all()
    )
    return [r[0] for r in rows]


def _resolve_title(db: Session, project_id: int, source_type: str, source_id: int) -> str:
    if source_type == "requirement":
        r = db.get(Requirement, source_id)
        return r.title if r and r.project_id == project_id else ""
    if source_type == "case":
        c = db.get(TestCase, source_id)
        return c.title if c and c.project_id == project_id else ""
    if source_type == "upload":
        d = db.get(KnowledgeDocument, source_id)
        return d.filename if d and d.project_id == project_id else ""
    return ""


def rag_search_hits(
    db: Session,
    project_id: int,
    query: str,
    top_k: int,
    *,
    source_type: str | None = None,
    source_id: int | None = None,
    upload_document_ids: list[int] | None = None,
) -> list[RagHitOut]:
    if upload_document_ids is not None and len(upload_document_ids) == 0:
        return []
    emb = get_embedding_runtime(db)
    if not emb.api_key.strip():
        raise AppError(
            "bad_request",
            "未配置 Embedding API Key，请在系统 AI 配置或 .env 中设置",
            status_code=400,
        )
    coll = milvus_store.ensure_collection(emb.dim)
    qv = embedding.embed_query(query, db, scene="检索向量化")
    pairs = milvus_store.search_vectors(
        coll,
        query_vector=qv,
        project_id=project_id,
        top_k=top_k,
        source_type=None if upload_document_ids is not None else source_type,
        source_id=None if upload_document_ids is not None else source_id,
        upload_source_ids=upload_document_ids,
    )
    out: list[RagHitOut] = []
    for chunk_id_str, score in pairs:
        cid = int(chunk_id_str)
        ch = db.get(IndexChunk, cid)
        if ch is None or ch.project_id != project_id:
            continue
        title = _resolve_title(db, project_id, ch.source_type, ch.source_id)
        out.append(
            RagHitOut(
                chunk_id=ch.id,
                text=ch.text,
                source_type=ch.source_type,
                source_id=ch.source_id,
                title_resolved=title or ch.source_type,
                score=score,
            )
        )
    return out
