from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.models.api_automation.api_automation import ApiDefinition
from app.models.identity.user import User
from app.models.project.project import ProjectMember, ProjectModule
from app.schemas.project.module import (
    ProjectModuleCreate,
    ProjectModuleOut,
    ProjectModuleTreeNode,
    ProjectModuleUpdate,
)
from app.services.project import modules as module_svc

router = APIRouter(prefix="/projects/{project_id}/modules", tags=["project-modules"])


def _out(row: ProjectModule) -> ProjectModuleOut:
    return ProjectModuleOut(
        id=row.id,
        project_id=row.project_id,
        parent_id=row.parent_id,
        name=row.name,
        description=row.description or "",
        sort_order=row.sort_order,
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=list[ProjectModuleOut])
def list_flat(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[ProjectModuleOut]:
    return [_out(r) for r in module_svc.list_modules(db, project_id)]


@router.get("/tree", response_model=list[ProjectModuleTreeNode])
def list_tree(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[ProjectModuleTreeNode]:
    rows = module_svc.list_modules(db, project_id)
    return [ProjectModuleTreeNode.model_validate(n) for n in module_svc.build_tree(rows)]


@router.post("/", response_model=ProjectModuleOut, status_code=201)
def create_module(
    project_id: int,
    body: ProjectModuleCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> ProjectModuleOut:
    row = module_svc.create_module(
        db,
        project_id=project_id,
        name=body.name,
        parent_id=body.parent_id,
        description=body.description or "",
        sort_order=body.sort_order,
        creator_id=user.id,
    )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch("/{module_id}", response_model=ProjectModuleOut)
def update_module(
    project_id: int,
    module_id: int,
    body: ProjectModuleUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> ProjectModuleOut:
    data = body.model_dump(exclude_unset=True)
    kwargs: dict = {"project_id": project_id, "module_id": module_id}
    if "name" in data:
        kwargs["name"] = data["name"]
    if "description" in data:
        kwargs["description"] = data["description"]
    if "sort_order" in data:
        kwargs["sort_order"] = data["sort_order"]
    if "parent_id" in data:
        kwargs["parent_id"] = data["parent_id"]
    row = module_svc.update_module(db, **kwargs)
    if "name" in data:
        (
            db.query(ApiDefinition)
            .filter(ApiDefinition.project_id == project_id, ApiDefinition.module_id == module_id)
            .update({"module": row.name}, synchronize_session=False)
        )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{module_id}", status_code=204)
def delete_module(
    project_id: int,
    module_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    force: bool = Query(False),
) -> None:
    module_svc.delete_module(db, project_id=project_id, module_id=module_id, force=force)
    db.commit()
