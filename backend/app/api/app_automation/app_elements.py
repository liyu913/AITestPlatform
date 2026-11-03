from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.app_automation.app_automation import AppElement
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.app_automation.app_automation import AppElementCreate, AppElementOut, AppElementUpdate
from app.schemas.page import Page
from app.user_messages import APP_ELEMENT_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/app-elements", tags=["app-elements"])


def _require(db: Session, project_id: int, element_id: int) -> AppElement:
    row = (
        db.query(AppElement)
        .filter(AppElement.id == element_id, AppElement.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", APP_ELEMENT_NOT_FOUND, status_code=404)
    return row


@router.get("/", response_model=Page[AppElementOut])
def list_elements(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    page_name: str | None = Query(None, max_length=128),
    search: str | None = Query(None, max_length=128),
) -> Page[AppElementOut]:
    p, ps = max(1, page), min(200, max(1, page_size))
    q = db.query(AppElement).filter(AppElement.project_id == project_id)
    if page_name:
        q = q.filter(AppElement.page_name == page_name.strip())
    if search and search.strip():
        s = f"%{search.strip()}%"
        q = q.filter((AppElement.name.like(s)) | (AppElement.selector.like(s)))
    total = q.count()
    rows = q.order_by(AppElement.page_name, AppElement.name).offset((p - 1) * ps).limit(ps).all()
    return Page[AppElementOut](
        items=[AppElementOut.model_validate(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/", response_model=AppElementOut, status_code=201)
def create_element(
    project_id: int,
    body: AppElementCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AppElementOut:
    row = AppElement(
        project_id=project_id,
        page_name=(body.page_name or "").strip(),
        name=body.name.strip(),
        strategy=body.strategy,
        selector=body.selector.strip(),
        description=body.description or "",
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return AppElementOut.model_validate(row)


@router.patch("/{element_id}", response_model=AppElementOut)
def update_element(
    project_id: int,
    element_id: int,
    body: AppElementUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppElementOut:
    row = _require(db, project_id, element_id)
    data = body.model_dump(exclude_unset=True)
    if "page_name" in data and data["page_name"] is not None:
        row.page_name = str(data["page_name"]).strip()
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "strategy" in data and data["strategy"] is not None:
        row.strategy = data["strategy"]
    if "selector" in data and data["selector"] is not None:
        row.selector = str(data["selector"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    db.commit()
    db.refresh(row)
    return AppElementOut.model_validate(row)


@router.delete("/{element_id}", status_code=204)
def delete_element(
    project_id: int,
    element_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require(db, project_id, element_id)
    db.delete(row)
    db.commit()
