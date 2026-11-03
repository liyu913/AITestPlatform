import app.deps as deps_mod
from app.models.knowledge.knowledge import KnowledgeDocument
from app.services.knowledge.document_extract import extract_knowledge_text
from app.services.file_storage import absolute_path_for_storage_key
from app.tasks.celery_app import celery_app
from app.tasks.knowledge.reindex_entity import reindex_source


def run_parse_document(doc_id: int) -> None:
    db = deps_mod.SessionLocal()
    project_id: int | None = None
    try:
        doc = db.get(KnowledgeDocument, doc_id)
        if doc is None:
            return
        doc.parse_status = "parsing"
        db.commit()
        path = absolute_path_for_storage_key(doc.storage_path)
        try:
            data = path.read_bytes()
            extract_knowledge_text(doc.filename, data)
        except OSError as e:
            doc.parse_status = "failed"
            doc.error_message = str(e)
            db.commit()
            return
        except ValueError as e:
            doc.parse_status = "failed"
            doc.error_message = str(e)
            db.commit()
            return
        doc.parse_status = "ready"
        doc.error_message = None
        db.commit()
        project_id = doc.project_id
    finally:
        db.close()
    if project_id is not None:
        reindex_source(project_id, "upload", doc_id)


@celery_app.task(name="parse_document")
def parse_document_task(doc_id: int) -> None:
    run_parse_document(doc_id)


@celery_app.task(name="reindex_source")
def reindex_source_task(project_id: int, source_type: str, source_id: int) -> None:
    reindex_source(project_id, source_type, source_id)
