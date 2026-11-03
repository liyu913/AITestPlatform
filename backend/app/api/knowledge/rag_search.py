from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.models.project.project import ProjectMember
from app.schemas.knowledge.rag import RagHitOut, RagSearchRequest
from app.services.knowledge.rag_retrieval import rag_search_hits, upload_document_ids_for_library

router = APIRouter(prefix="/projects/{project_id}/rag", tags=["rag"])


@router.post("/search", response_model=list[RagHitOut])
def rag_search(
    project_id: int,
    body: RagSearchRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[RagHitOut]:
    upload_ids: list[int] | None = None
    if body.knowledge_library_id is not None:
        upload_ids = upload_document_ids_for_library(
            db, project_id, body.knowledge_library_id
        )
    return rag_search_hits(
        db,
        project_id,
        body.query,
        body.top_k,
        source_type=body.source_type,
        source_id=body.source_id,
        upload_document_ids=upload_ids,
    )
