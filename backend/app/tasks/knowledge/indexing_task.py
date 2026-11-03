from app.services.knowledge import indexing
from app.tasks.celery_app import celery_app


@celery_app.task(name="index_entity")
def run_index_entity_task(project_id: int, source_type: str, source_id: int) -> None:
    indexing.run_reindex(project_id, source_type, source_id)
