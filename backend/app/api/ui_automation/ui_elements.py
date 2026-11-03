from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiElement
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.ui_automation.ui_automation import UiElementCreate, UiElementOut, UiElementUpdate
from app.user_messages import UI_ELEMENT_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/ui-elements", tags=["ui-elements"])


def _require(db: Session, project_id: int, element_id: int) -> UiElement:
    row = (
        db.query(UiElement)
        .filter(UiElement.id == element_id, UiElement.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", UI_ELEMENT_NOT_FOUND, status_code=404)
    return row


@router.get("/", response_model=Page[UiElementOut])
def list_elements(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    page_name: str | None = Query(None, max_length=128),
    search: str | None = Query(None, max_length=128),
) -> Page[UiElementOut]:
    p, ps = max(1, page), min(200, max(1, page_size))
    q = db.query(UiElement).filter(UiElement.project_id == project_id)
    if page_name:
        q = q.filter(UiElement.page_name == page_name.strip())
    if search and search.strip():
        s = f"%{search.strip()}%"
        q = q.filter((UiElement.name.like(s)) | (UiElement.selector.like(s)))
    total = q.count()
    rows = q.order_by(UiElement.page_name, UiElement.name).offset((p - 1) * ps).limit(ps).all()
    return Page[UiElementOut](
        items=[UiElementOut.model_validate(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/", response_model=UiElementOut, status_code=201)
def create_element(
    project_id: int,
    body: UiElementCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiElementOut:
    row = UiElement(
        project_id=project_id,
        page_name=(body.page_name or "").strip(),
        name=body.name.strip(),
        selector=body.selector.strip(),
        description=body.description or "",
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return UiElementOut.model_validate(row)


@router.patch("/{element_id}", response_model=UiElementOut)
def update_element(
    project_id: int,
    element_id: int,
    body: UiElementUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiElementOut:
    row = _require(db, project_id, element_id)
    data = body.model_dump(exclude_unset=True)
    if "page_name" in data and data["page_name"] is not None:
        row.page_name = str(data["page_name"]).strip()
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    if "selector" in data and data["selector"] is not None:
        row.selector = str(data["selector"]).strip()
    if "description" in data and data["description"] is not None:
        row.description = data["description"]
    db.commit()
    db.refresh(row)
    return UiElementOut.model_validate(row)


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
