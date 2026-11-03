from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.user_messages import (
    DOCUMENT_NOT_FOUND,
    KNOWLEDGE_FILE_TYPE_INVALID,
    KNOWLEDGE_LIBRARY_NOT_FOUND,
)
from app.models.knowledge.index_chunk import IndexChunk
from app.models.knowledge.knowledge import KnowledgeDocument
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.knowledge.knowledge import (
    IndexChunkOut,
    KnowledgeDocOut,
    KnowledgeDocPatch,
    KnowledgeIndexStatusOut,
    KnowledgeLibraryCreate,
    KnowledgeLibraryOut,
    KnowledgeLibraryPatch,
)
from app.services.knowledge.document_extract import knowledge_allowed_suffixes
from app.services.file_storage import remove_storage_key, save_project_upload
from app.services.knowledge import milvus_store
from app.services.ai.effective_ai_settings import (
    effective_embedding_has_api_key,
    get_embedding_runtime,
)
from app.tasks.knowledge.parse_and_index import parse_document_task, reindex_source_task

router = APIRouter(prefix="/projects/{project_id}/knowledge", tags=["knowledge"])

_ALLOWED = set(knowledge_allowed_suffixes())


def _clean_category(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _resolve_display_filename(raw_name: str, display_name: str | None) -> str:
    cleaned = (display_name or "").strip()
    if not cleaned:
        return raw_name
    if len(cleaned) > 512:
        cleaned = cleaned[:512]
    orig_suf = Path(raw_name).suffix
    if orig_suf and not cleaned.lower().endswith(orig_suf.lower()):
        # Keep room for the original suffix used by extract_knowledge_text.
        max_stem = 512 - len(orig_suf)
        cleaned = f"{cleaned[:max_stem]}{orig_suf}"
    return cleaned


def _apply_doc_category_filters(
    q,
    *,
    category: str | None,
    uncategorized: bool,
):
    if uncategorized:
        return q.filter(KnowledgeDocument.category.is_(None))
    if category is not None:
        cleaned = _clean_category(category)
        if cleaned is not None:
            return q.filter(KnowledgeDocument.category == cleaned)
    return q


def _first_library(db: Session, project_id: int) -> KnowledgeLibrary:
    lib = (
        db.query(KnowledgeLibrary)
        .filter(KnowledgeLibrary.project_id == project_id)
        .order_by(KnowledgeLibrary.id.asc())
        .first()
    )
    if lib is None:
        lib = KnowledgeLibrary(project_id=project_id, name="默认知识库", description=None)
        db.add(lib)
        db.commit()
        db.refresh(lib)
    return lib


def _get_library(db: Session, project_id: int, library_id: int) -> KnowledgeLibrary:
    lib = (
        db.query(KnowledgeLibrary)
        .filter(
            KnowledgeLibrary.id == library_id,
            KnowledgeLibrary.project_id == project_id,
        )
        .first()
    )
    if lib is None:
        raise AppError("not_found", KNOWLEDGE_LIBRARY_NOT_FOUND, status_code=404)
    return lib


def _status_counts(db: Session, library_id: int) -> tuple[int, int, int, int]:
    rows = (
        db.query(KnowledgeDocument.parse_status, func.count(KnowledgeDocument.id))
        .filter(KnowledgeDocument.library_id == library_id)
        .group_by(KnowledgeDocument.parse_status)
        .all()
    )
    ready = failed = parsing = total = 0
    for status, count in rows:
        n = int(count or 0)
        total += n
        if status == "ready":
            ready += n
        elif status == "failed":
            failed += n
        else:
            parsing += n
    return total, ready, failed, parsing


def _library_out(db: Session, lib: KnowledgeLibrary) -> KnowledgeLibraryOut:
    total, ready, failed, parsing = _status_counts(db, lib.id)
    return KnowledgeLibraryOut(
        id=lib.id,
        project_id=lib.project_id,
        name=lib.name,
        description=lib.description,
        document_count=total,
        ready_count=ready,
        failed_count=failed,
        parsing_count=parsing,
        chunk_mode=lib.chunk_mode or "window",
        chunk_window=int(lib.chunk_window),
        chunk_overlap=int(lib.chunk_overlap),
        reindex_required=bool(lib.reindex_required),
        created_at=lib.created_at,
        updated_at=lib.updated_at,
    )


async def _persist_upload(
    db: Session,
    project_id: int,
    library_id: int,
    file: UploadFile,
    uploaded_by: int,
    category: str | None = None,
    display_name: str | None = None,
) -> KnowledgeDocument:
    raw_name = file.filename or "document"
    suf = Path(raw_name).suffix.lower()
    if suf not in _ALLOWED:
        raise AppError("bad_request", KNOWLEDGE_FILE_TYPE_INVALID, status_code=400)
    data = await file.read()
    rel, size = save_project_upload(project_id, raw_name, data)
    doc = KnowledgeDocument(
        project_id=project_id,
        library_id=library_id,
        filename=_resolve_display_filename(raw_name, display_name),
        storage_path=rel,
        mime=file.content_type,
        size_bytes=size,
        category=_clean_category(category),
        uploaded_by=uploaded_by,
        parse_status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    parse_document_task.delay(doc.id)
    db.refresh(doc)
    return doc


@router.get("/libraries", response_model=list[KnowledgeLibraryOut])
def list_libraries(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
):
    libs = (
        db.query(KnowledgeLibrary)
        .filter(KnowledgeLibrary.project_id == project_id)
        .order_by(KnowledgeLibrary.updated_at.desc(), KnowledgeLibrary.id.desc())
        .all()
    )
    return [_library_out(db, lib) for lib in libs]


@router.post("/libraries", response_model=KnowledgeLibraryOut, status_code=201)
def create_library(
    project_id: int,
    body: KnowledgeLibraryCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    lib = KnowledgeLibrary(
        project_id=project_id,
        name=body.name.strip(),
        description=(body.description.strip() if body.description else None),
        chunk_mode=body.chunk_mode,
        chunk_window=body.chunk_window,
        chunk_overlap=body.chunk_overlap,
    )
    db.add(lib)
    db.commit()
    db.refresh(lib)
    return _library_out(db, lib)


@router.patch("/libraries/{library_id}", response_model=KnowledgeLibraryOut)
def patch_library(
    project_id: int,
    library_id: int,
    body: KnowledgeLibraryPatch,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    lib = _get_library(db, project_id, library_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        lib.name = str(data["name"]).strip()
    if "description" in data:
        lib.description = data["description"].strip() if data["description"] else None
    chunk_changed = False
    if "chunk_mode" in data and data["chunk_mode"] is not None and data["chunk_mode"] != lib.chunk_mode:
        lib.chunk_mode = data["chunk_mode"]
        chunk_changed = True
    if "chunk_window" in data and data["chunk_window"] is not None and int(data["chunk_window"]) != lib.chunk_window:
        lib.chunk_window = int(data["chunk_window"])
        chunk_changed = True
    if (
        "chunk_overlap" in data
        and data["chunk_overlap"] is not None
        and int(data["chunk_overlap"]) != lib.chunk_overlap
    ):
        lib.chunk_overlap = int(data["chunk_overlap"])
        chunk_changed = True
    if lib.chunk_overlap >= lib.chunk_window:
        raise AppError("bad_request", "分块重叠必须小于分块大小", status_code=400)
    if chunk_changed:
        total, _, _, _ = _status_counts(db, lib.id)
        if total > 0:
            lib.reindex_required = True
    db.commit()
    db.refresh(lib)
    return _library_out(db, lib)


@router.delete("/libraries/{library_id}", status_code=204)
def delete_library(
    project_id: int,
    library_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    lib = _get_library(db, project_id, library_id)
    docs = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.library_id == lib.id,
        )
        .all()
    )
    storage_keys = [doc.storage_path for doc in docs]
    for doc in docs:
        _purge_document_chunks(db, project_id, doc.id)
        db.delete(doc)
    db.delete(lib)
    db.commit()
    for key in storage_keys:
        remove_storage_key(key)
    return None


@router.post("/libraries/{library_id}/reindex", status_code=202)
def reindex_library_documents(
    project_id: int,
    library_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    lib = _get_library(db, project_id, library_id)
    docs = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.project_id == project_id,
            KnowledgeDocument.library_id == library_id,
        )
        .all()
    )
    for doc in docs:
        reindex_source_task.delay(project_id, "upload", doc.id)
    lib.reindex_required = False
    db.commit()
    return {"queued": len(docs)}


@router.get("/libraries/{library_id}/documents", response_model=list[KnowledgeDocOut])
def list_library_documents(
    project_id: int,
    library_id: int,
    category: str | None = Query(default=None, max_length=128),
    uncategorized: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
):
    _get_library(db, project_id, library_id)
    q = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.project_id == project_id,
        KnowledgeDocument.library_id == library_id,
    )
    q = _apply_doc_category_filters(q, category=category, uncategorized=uncategorized)
    return q.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id.desc()).all()


@router.post("/libraries/{library_id}/upload", response_model=KnowledgeDocOut, status_code=201)
async def upload_to_library(
    project_id: int,
    library_id: int,
    file: UploadFile = File(...),
    category: str | None = Form(default=None),
    name: str | None = Form(default=None),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
):
    _get_library(db, project_id, library_id)
    return await _persist_upload(
        db, project_id, library_id, file, user.id, category=category, display_name=name
    )


@router.post("/upload", response_model=KnowledgeDocOut, status_code=201)
async def upload_document_legacy(
    project_id: int,
    file: UploadFile = File(...),
    category: str | None = Form(default=None),
    name: str | None = Form(default=None),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
):
    """上传到该项目第一个知识库（通常为「默认知识库」）。"""
    lib = _first_library(db, project_id)
    return await _persist_upload(
        db, project_id, lib.id, file, user.id, category=category, display_name=name
    )


@router.get("/index-status", response_model=KnowledgeIndexStatusOut)
def knowledge_index_status(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
):
    _ = project_id
    return KnowledgeIndexStatusOut(embedding_ready=effective_embedding_has_api_key(db))


@router.get("/", response_model=list[KnowledgeDocOut])
def list_documents(
    project_id: int,
    category: str | None = Query(default=None, max_length=128),
    uncategorized: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
):
    q = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
    q = _apply_doc_category_filters(q, category=category, uncategorized=uncategorized)
    return q.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id.desc()).all()


@router.get("/chunks", response_model=list[IndexChunkOut])
def list_chunks(
    project_id: int,
    library_id: int | None = Query(default=None),
    document_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
):
    q = (
        db.query(IndexChunk, KnowledgeDocument, KnowledgeLibrary)
        .outerjoin(
            KnowledgeDocument,
            (KnowledgeDocument.id == IndexChunk.source_id)
            & (KnowledgeDocument.project_id == project_id),
        )
        .outerjoin(KnowledgeLibrary, KnowledgeLibrary.id == KnowledgeDocument.library_id)
        .filter(
            IndexChunk.project_id == project_id,
            IndexChunk.source_type == "upload",
        )
    )
    if document_id is not None:
        q = q.filter(IndexChunk.source_id == document_id)
    if library_id is not None:
        q = q.filter(KnowledgeDocument.library_id == library_id)
    rows = q.order_by(IndexChunk.source_id.asc(), IndexChunk.chunk_index.asc()).all()
    out: list[IndexChunkOut] = []
    for chunk, doc, lib in rows:
        out.append(
            IndexChunkOut(
                id=chunk.id,
                document_id=chunk.source_id,
                document_filename=doc.filename if doc else "",
                library_id=lib.id if lib else (doc.library_id if doc else None),
                library_name=lib.name if lib else None,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                index_status=chunk.index_status,
                embedding_model=chunk.embedding_model,
                created_at=chunk.created_at,
            )
        )
    return out


def _purge_document_chunks(db: Session, project_id: int, doc_id: int) -> None:
    existing = (
        db.query(IndexChunk)
        .filter(
            IndexChunk.project_id == project_id,
            IndexChunk.source_type == "upload",
            IndexChunk.source_id == doc_id,
        )
        .all()
    )
    if not existing:
        return
    old_ids = [str(c.id) for c in existing]
    try:
        emb = get_embedding_runtime(db)
        coll = milvus_store.ensure_collection(emb.dim)
        milvus_store.delete_by_chunk_ids(coll, old_ids)
    except Exception:
        pass
    for c in existing:
        db.delete(c)


@router.patch("/{doc_id}", response_model=KnowledgeDocOut)
def patch_document(
    project_id: int,
    doc_id: int,
    body: KnowledgeDocPatch,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    doc = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.id == doc_id, KnowledgeDocument.project_id == project_id)
        .first()
    )
    if doc is None:
        raise AppError("not_found", DOCUMENT_NOT_FOUND, status_code=404)
    data = body.model_dump(exclude_unset=True)
    if "category" in data:
        doc.category = _clean_category(data["category"])
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/{doc_id}", status_code=204)
def delete_document(
    project_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    doc = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.id == doc_id, KnowledgeDocument.project_id == project_id)
        .first()
    )
    if doc is None:
        raise AppError("not_found", DOCUMENT_NOT_FOUND, status_code=404)
    storage_key = doc.storage_path
    _purge_document_chunks(db, project_id, doc_id)
    db.delete(doc)
    db.commit()
    remove_storage_key(storage_key)
    return None


@router.post("/{doc_id}/reindex", status_code=202)
def manual_reindex_document(
    project_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
):
    doc = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.id == doc_id, KnowledgeDocument.project_id == project_id)
        .first()
    )
    if doc is None:
        raise AppError("not_found", DOCUMENT_NOT_FOUND, status_code=404)
    reindex_source_task.delay(project_id, "upload", doc_id)
    return {"queued": True}
