"""Enqueue RAG reindex for a source entity (Celery worker)."""


def enqueue_reindex(project_id: int, source_type: str, source_id: int) -> None:
    from app.tasks.knowledge.indexing_task import run_index_entity_task

    run_index_entity_task.delay(project_id, source_type, source_id)


def reindex_source(project_id: int, source_type: str, source_id: int) -> None:
    """Run indexing in the current worker so a stale sibling worker cannot steal the job."""
    from app.services.knowledge import indexing

    indexing.run_reindex(project_id, source_type, source_id)
