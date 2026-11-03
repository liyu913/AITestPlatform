from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.project.project import ProjectModule
from app.user_messages import (
    PROJECT_MODULE_CYCLE,
    PROJECT_MODULE_DEPTH_LIMIT,
    PROJECT_MODULE_HAS_CHILDREN,
    PROJECT_MODULE_NAME_EXISTS,
    PROJECT_MODULE_NOT_FOUND,
    PROJECT_MODULE_NOT_IN_PROJECT,
)

MAX_MODULE_DEPTH = 3


def get_module(db: Session, project_id: int, module_id: int) -> ProjectModule:
    row = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if row is None:
        raise AppError("not_found", PROJECT_MODULE_NOT_FOUND, status_code=404)
    if row.project_id != project_id:
        raise AppError("not_found", PROJECT_MODULE_NOT_IN_PROJECT, status_code=404)
    return row


def list_modules(db: Session, project_id: int) -> list[ProjectModule]:
    return (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == project_id)
        .order_by(ProjectModule.sort_order, ProjectModule.id)
        .all()
    )


def _depth_of(db: Session, project_id: int, module_id: int | None) -> int:
    """1-based depth of module_id; 0 if None (would-be root's parent)."""
    if module_id is None:
        return 0
    depth = 0
    cur: int | None = module_id
    seen: set[int] = set()
    while cur is not None:
        if cur in seen:
            raise AppError("invalid", PROJECT_MODULE_CYCLE, status_code=400)
        seen.add(cur)
        depth += 1
        row = get_module(db, project_id, cur)
        cur = row.parent_id
    return depth


def _descendant_ids(db: Session, project_id: int, root_id: int) -> set[int]:
    rows = list_modules(db, project_id)
    by_parent: dict[int | None, list[int]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_id, []).append(r.id)
    out: set[int] = set()
    stack = [root_id]
    while stack:
        mid = stack.pop()
        for child in by_parent.get(mid, []):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


def _assert_unique_name(
    db: Session,
    project_id: int,
    parent_id: int | None,
    name: str,
    *,
    exclude_id: int | None = None,
) -> None:
    q = db.query(ProjectModule).filter(
        ProjectModule.project_id == project_id,
        ProjectModule.parent_id == parent_id,
        ProjectModule.name == name,
    )
    if exclude_id is not None:
        q = q.filter(ProjectModule.id != exclude_id)
    if q.first() is not None:
        raise AppError("conflict", PROJECT_MODULE_NAME_EXISTS, status_code=409)


def create_module(
    db: Session,
    *,
    project_id: int,
    name: str,
    parent_id: int | None,
    description: str,
    sort_order: int,
    creator_id: int | None,
) -> ProjectModule:
    clean = name.strip()
    if parent_id is not None:
        get_module(db, project_id, parent_id)
    parent_depth = _depth_of(db, project_id, parent_id)
    if parent_depth + 1 > MAX_MODULE_DEPTH:
        raise AppError("invalid", PROJECT_MODULE_DEPTH_LIMIT, status_code=400)
    _assert_unique_name(db, project_id, parent_id, clean)
    row = ProjectModule(
        project_id=project_id,
        parent_id=parent_id,
        name=clean,
        description=description or "",
        sort_order=sort_order,
        creator_id=creator_id,
    )
    db.add(row)
    db.flush()
    return row


def update_module(
    db: Session,
    *,
    project_id: int,
    module_id: int,
    name: str | None = None,
    parent_id: int | None | object = ...,
    description: str | None = None,
    sort_order: int | None = None,
) -> ProjectModule:
    row = get_module(db, project_id, module_id)
    new_parent = row.parent_id if parent_id is ... else parent_id
    new_name = row.name if name is None else name.strip()

    if parent_id is not ...:
        if new_parent is not None:
            get_module(db, project_id, new_parent)
        if new_parent == row.id:
            raise AppError("invalid", PROJECT_MODULE_CYCLE, status_code=400)
        if new_parent is not None and new_parent in _descendant_ids(db, project_id, row.id):
            raise AppError("invalid", PROJECT_MODULE_CYCLE, status_code=400)
        # subtree depth: this node becomes parent_depth+1, children add more
        parent_depth = _depth_of(db, project_id, new_parent)
        subtree_extra = _max_subtree_depth(db, project_id, row.id)
        if parent_depth + subtree_extra > MAX_MODULE_DEPTH:
            raise AppError("invalid", PROJECT_MODULE_DEPTH_LIMIT, status_code=400)
        row.parent_id = new_parent

    if name is not None or parent_id is not ...:
        _assert_unique_name(db, project_id, row.parent_id, new_name, exclude_id=row.id)
        row.name = new_name

    if description is not None:
        row.description = description
    if sort_order is not None:
        row.sort_order = sort_order
    db.flush()
    return row


def _max_subtree_depth(db: Session, project_id: int, root_id: int) -> int:
    """Depth of the deepest node in the subtree, counting root as 1."""
    rows = list_modules(db, project_id)
    by_parent: dict[int | None, list[int]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_id, []).append(r.id)

    def walk(mid: int) -> int:
        kids = by_parent.get(mid, [])
        if not kids:
            return 1
        return 1 + max(walk(c) for c in kids)

    return walk(root_id)


def delete_module(db: Session, *, project_id: int, module_id: int, force: bool = False) -> None:
    row = get_module(db, project_id, module_id)
    child = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == project_id, ProjectModule.parent_id == module_id)
        .first()
    )
    if child is not None and not force:
        raise AppError("conflict", PROJECT_MODULE_HAS_CHILDREN, status_code=409)
    db.delete(row)
    db.flush()


def build_tree(rows: list[ProjectModule]) -> list[dict]:
    by_parent: dict[int | None, list[ProjectModule]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_id, []).append(r)
    for kids in by_parent.values():
        kids.sort(key=lambda x: (x.sort_order, x.id))

    def node(r: ProjectModule) -> dict:
        return {
            "id": r.id,
            "parent_id": r.parent_id,
            "name": r.name,
            "description": r.description or "",
            "sort_order": r.sort_order,
            "children": [node(c) for c in by_parent.get(r.id, [])],
        }

    return [node(r) for r in by_parent.get(None, [])]
