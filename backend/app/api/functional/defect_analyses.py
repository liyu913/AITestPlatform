from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.defect import Defect
from app.models.functional.defect_analysis import DefectAnalysis
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.schemas.functional.defect import DefectOut
from app.schemas.functional.defect_analysis import (
    AnalysisCreate,
    AnalysisOut,
    AnalysisUpdate,
    SubmissionIn,
    SubmissionOut,
)
from app.services.functional import defect_analysis as service

router = APIRouter(prefix="/projects/{project_id}", tags=["defect-analysis"])


def _require_analysis(db: Session, project_id: int, analysis_id: int) -> DefectAnalysis:
    row = (
        db.query(DefectAnalysis)
        .filter(DefectAnalysis.id == analysis_id, DefectAnalysis.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", "分析记录不存在", status_code=404)
    return row


def _require_defect(db: Session, project_id: int, defect_id: int) -> Defect:
    row = (
        db.query(Defect)
        .filter(Defect.id == defect_id, Defect.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", "缺陷不存在", status_code=404)
    return row


@router.get("/defect-analyses/", response_model=list[AnalysisOut])
def list_analyses(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    status: str | None = Query(None, max_length=32),
    category: str | None = Query(None, max_length=32),
) -> list[AnalysisOut]:
    rows = service.list_analyses(db, project_id=project_id, status=status, category=category)
    return [AnalysisOut.model_validate(row) for row in rows]


@router.post("/defect-analyses/", response_model=AnalysisOut, status_code=201)
def create_analysis(
    project_id: int,
    payload: AnalysisCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AnalysisOut:
    row = service.create_analysis(
        db, project_id=project_id, payload=payload, user_id=user.id
    )
    return AnalysisOut.model_validate(row)


@router.get("/defect-analyses/{analysis_id}", response_model=AnalysisOut)
def get_analysis(
    project_id: int,
    analysis_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> AnalysisOut:
    return AnalysisOut.model_validate(_require_analysis(db, project_id, analysis_id))


@router.patch("/defect-analyses/{analysis_id}", response_model=AnalysisOut)
def update_analysis(
    project_id: int,
    analysis_id: int,
    payload: AnalysisUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AnalysisOut:
    row = _require_analysis(db, project_id, analysis_id)
    updated = service.update_analysis(db, analysis=row, payload=payload, user_id=user.id)
    return AnalysisOut.model_validate(updated)


@router.post("/defect-analyses/{analysis_id}/convert", response_model=DefectOut)
def convert_analysis(
    project_id: int,
    analysis_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> DefectOut:
    row = _require_analysis(db, project_id, analysis_id)
    defect = service.convert_to_defect(db, analysis=row, user_id=user.id)
    return DefectOut.model_validate(defect)


@router.get("/defects/pending-submission", response_model=list[DefectOut])
def pending_submission(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[DefectOut]:
    return [
        DefectOut.model_validate(row)
        for row in service.pending_submission(db, project_id=project_id)
    ]


@router.get("/defects/{defect_id}/submissions", response_model=list[SubmissionOut])
def list_submissions(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[SubmissionOut]:
    _require_defect(db, project_id, defect_id)
    rows = service.list_submissions(db, project_id=project_id, defect_id=defect_id)
    return [SubmissionOut.model_validate(row) for row in rows]


@router.post("/defects/{defect_id}/submissions", response_model=SubmissionOut)
def submit_defect(
    project_id: int,
    defect_id: int,
    payload: SubmissionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> SubmissionOut:
    defect = _require_defect(db, project_id, defect_id)
    row = service.submit_defect(
        db,
        defect=defect,
        target=payload.target,
        request_id=payload.request_id,
        connector=payload.connector,
        user_id=user.id,
    )
    return SubmissionOut.model_validate(row)
