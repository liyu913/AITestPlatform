from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.models.project.project import ProjectMember
from app.schemas.functional.test_plan import RequirementCoverageOut
from app.services.functional.coverage_stats import requirement_coverage_rows

router = APIRouter(prefix="/projects/{project_id}/coverage", tags=["coverage"])


@router.get("/requirements", response_model=list[RequirementCoverageOut])
def requirement_coverage(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    cycle_id: int | None = Query(None, ge=1),
) -> list[RequirementCoverageOut]:
    return requirement_coverage_rows(db, project_id, cycle_id)
