import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.user_messages import (
    CANNOT_DEMOTE_LAST_ADMIN,
    CANNOT_REMOVE_LAST_ADMIN,
    MEMBER_NOT_FOUND,
    MEMBER_USER_MISSING,
    PROJECT_NOT_FOUND,
    USER_ALREADY_IN_PROJECT,
    USER_NOT_FOUND,
)
from app.models.knowledge.knowledge_library import KnowledgeLibrary
from app.models.project.project import Project, ProjectMember
from app.models.identity.user import User
from app.schemas.project.project import (
    MemberAdd,
    MemberOut,
    MemberPatch,
    ProjectCreate,
    ProjectListItemOut,
    ProjectOut,
)
from app.services.knowledge import milvus_store
from app.services.file_storage import remove_project_upload_dir

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


def _admin_count(db: Session, project_id: int) -> int:
    return (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.role == "admin")
        .count()
    )


def _member_out(db: Session, pm: ProjectMember) -> MemberOut:
    u = db.get(User, pm.user_id)
    if u is None:
        raise AppError("internal", MEMBER_USER_MISSING, status_code=500)
    return MemberOut(user_id=u.id, username=u.username, role=pm.role)


def _project_list_item(project: Project, my_role: str) -> ProjectListItemOut:
    return ProjectListItemOut(
        id=project.id,
        name=project.name,
        description=project.description,
        owner_id=project.owner_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        my_role=my_role,
    )


def _cleanup_project_external_resources(project_id: int) -> None:
    try:
        coll = milvus_store.open_collection()
        if coll is not None:
            milvus_store.delete_by_project_id(coll, project_id)
    except Exception:
        log.exception("删除项目 Milvus 向量失败 project_id=%s", project_id)
    try:
        remove_project_upload_dir(project_id)
    except Exception:
        log.exception("删除项目上传目录失败 project_id=%s", project_id)


@router.post("/", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    p = Project(name=body.name, description=body.description, owner_id=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id, role="admin"))
    db.add(KnowledgeLibrary(project_id=p.id, name="默认知识库", description=None))
    db.commit()
    db.refresh(p)
    return p


@router.get("/", response_model=list[ProjectListItemOut])
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectListItemOut]:
    rows = (
        db.query(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .filter(ProjectMember.user_id == user.id)
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .all()
    )
    return [_project_list_item(project, role) for project, role in rows]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise AppError("not_found", PROJECT_NOT_FOUND, status_code=404)
    return p


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    p = db.get(Project, project_id)
    if p is None:
        raise AppError("not_found", PROJECT_NOT_FOUND, status_code=404)
    _cleanup_project_external_resources(project_id)
    db.delete(p)
    db.commit()


@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[MemberOut]:
    rows = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.user_id)
        .all()
    )
    return [_member_out(db, pm) for pm in rows]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    project_id: int,
    body: MemberAdd,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> MemberOut:
    target = db.query(User).filter(User.username == body.username).first()
    if target is None:
        raise AppError("not_found", USER_NOT_FOUND, status_code=404)
    dup = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == target.id)
        .first()
    )
    if dup is not None:
        raise AppError("conflict", USER_ALREADY_IN_PROJECT, status_code=409)
    pm = ProjectMember(project_id=project_id, user_id=target.id, role=body.role)
    db.add(pm)
    db.commit()
    db.refresh(pm)
    return _member_out(db, pm)


@router.patch("/{project_id}/members/{member_user_id}", response_model=MemberOut)
def patch_member(
    project_id: int,
    member_user_id: int,
    body: MemberPatch,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> MemberOut:
    m = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == member_user_id)
        .first()
    )
    if m is None:
        raise AppError("not_found", MEMBER_NOT_FOUND, status_code=404)
    if m.role == "admin" and body.role != "admin" and _admin_count(db, project_id) <= 1:
        raise AppError("bad_request", CANNOT_DEMOTE_LAST_ADMIN, status_code=400)
    m.role = body.role
    db.commit()
    db.refresh(m)
    return _member_out(db, m)


@router.delete("/{project_id}/members/{member_user_id}", status_code=204)
def remove_member(
    project_id: int,
    member_user_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    m = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == member_user_id)
        .first()
    )
    if m is None:
        raise AppError("not_found", MEMBER_NOT_FOUND, status_code=404)
    if m.role == "admin" and _admin_count(db, project_id) <= 1:
        raise AppError("bad_request", CANNOT_REMOVE_LAST_ADMIN, status_code=400)
    db.delete(m)
    db.commit()
