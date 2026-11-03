from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.defect import Defect, DefectAttachment
from app.models.project.project import ProjectMember
from app.models.functional.requirement import Requirement
from app.models.functional.testcase import TestCase
from app.models.functional.test_plan import ExecutionAttachment, TestCycle, TestCycleItem, TestPlan
from app.models.identity.user import User
from app.schemas.functional.defect import DefectAttachmentOut, DefectCreate, DefectOut, DefectUpdate
from app.schemas.page import Page
from app.services.file_storage import (
    absolute_path_for_storage_key,
    remove_storage_key,
    save_defect_attachment,
)
from app.user_messages import (
    CYCLE_ASSIGNEE_NOT_MEMBER,
    DEFECT_ATTACHMENT_NOT_FOUND,
    DEFECT_ATTACHMENT_TOO_LARGE,
    DEFECT_ATTACHMENT_TYPE_INVALID,
    DEFECT_FROM_ITEM_REQUIRES_FAILED,
    DEFECT_NOT_FOUND,
    REQUIREMENT_NOT_IN_PROJECT,
    TEST_CASE_NOT_FOUND,
    TEST_CYCLE_ITEM_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/defects", tags=["defects"])

_ALLOWED_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md", ".log", ".json", ".zip", ".docx"}
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_defect(db: Session, project_id: int, defect_id: int) -> Defect:
    row = (
        db.query(Defect)
        .filter(Defect.id == defect_id, Defect.project_id == project_id)
        .options(selectinload(Defect.attachments))
        .first()
    )
    if row is None:
        raise AppError("not_found", DEFECT_NOT_FOUND, status_code=404)
    return row


def _case_title_map(db: Session, project_id: int, case_ids: set[int]) -> dict[int, str]:
    ids = {cid for cid in case_ids if cid}
    if not ids:
        return {}
    rows = (
        db.query(TestCase.id, TestCase.title)
        .filter(TestCase.project_id == project_id, TestCase.id.in_(ids))
        .all()
    )
    return {int(cid): title for cid, title in rows}


def _out_one(db: Session, project_id: int, row: Defect) -> DefectOut:
    titles = _case_title_map(db, project_id, {row.case_id} if row.case_id else set())
    return _defect_to_out(row, titles)


def _defect_to_out(row: Defect, titles: dict[int, str] | None = None) -> DefectOut:
    atts = sorted(row.attachments, key=lambda a: a.id) if row.attachments else []
    case_title = titles.get(row.case_id) if row.case_id is not None and titles else None
    return DefectOut(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        description=row.description or "",
        severity=row.severity,
        status=row.status,
        case_id=row.case_id,
        case_title=case_title,
        cycle_item_id=row.cycle_item_id,
        requirement_id=row.requirement_id,
        reporter_id=row.reporter_id,
        assignee_id=row.assignee_id,
        external_system=row.external_system,
        external_id=row.external_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        attachments=[DefectAttachmentOut.model_validate(a) for a in atts],
    )


def _ensure_member(db: Session, project_id: int, user_id: int | None) -> None:
    if user_id is None:
        return
    row = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if row is None:
        raise AppError("bad_request", CYCLE_ASSIGNEE_NOT_MEMBER, status_code=400)


def _validate_refs(
    db: Session,
    project_id: int,
    *,
    case_id: int | None = None,
    requirement_id: int | None = None,
    cycle_item_id: int | None = None,
) -> None:
    if case_id is not None:
        case = db.query(TestCase).filter(TestCase.id == case_id, TestCase.project_id == project_id).first()
        if case is None:
            raise AppError("bad_request", TEST_CASE_NOT_FOUND, status_code=400)
    if requirement_id is not None:
        req = (
            db.query(Requirement)
            .filter(Requirement.id == requirement_id, Requirement.project_id == project_id)
            .first()
        )
        if req is None:
            raise AppError("bad_request", REQUIREMENT_NOT_IN_PROJECT, status_code=400)
    if cycle_item_id is not None:
        item = (
            db.query(TestCycleItem)
            .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
            .join(TestPlan, TestCycle.plan_id == TestPlan.id)
            .filter(TestCycleItem.id == cycle_item_id, TestPlan.project_id == project_id)
            .first()
        )
        if item is None:
            raise AppError("bad_request", TEST_CYCLE_ITEM_NOT_FOUND, status_code=400)


def _validate_upload(filename: str, mime: str, size: int) -> None:
    if size > _MAX_ATTACHMENT_BYTES:
        raise AppError("bad_request", DEFECT_ATTACHMENT_TOO_LARGE, status_code=400)
    ext = Path(filename).suffix.lower()
    mime_l = (mime or "").lower()
    if mime_l not in _ALLOWED_MIMES and ext not in _ALLOWED_EXT:
        raise AppError("bad_request", DEFECT_ATTACHMENT_TYPE_INVALID, status_code=400)


def _copy_execution_attachments(
    db: Session,
    *,
    project_id: int,
    defect: Defect,
    cycle_item_id: int,
    uploader_id: int | None,
) -> None:
    srcs = (
        db.query(ExecutionAttachment)
        .filter(ExecutionAttachment.cycle_item_id == cycle_item_id)
        .order_by(ExecutionAttachment.id)
        .all()
    )
    for src in srcs:
        path = absolute_path_for_storage_key(src.storage_key)
        if not path.is_file():
            continue
        data = path.read_bytes()
        storage_key, size = save_defect_attachment(project_id, defect.id, src.filename, data)
        db.add(
            DefectAttachment(
                defect_id=defect.id,
                filename=src.filename,
                mime=src.mime,
                storage_key=storage_key,
                size_bytes=size,
                uploader_id=uploader_id,
            )
        )


@router.get("/", response_model=Page[DefectOut])
def list_defects(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, max_length=64),
    search: str | None = Query(None, max_length=256),
    case_id: int | None = Query(None, ge=1),
) -> Page[DefectOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(Defect).filter(Defect.project_id == project_id)
    if status:
        filtered = filtered.filter(Defect.status == status)
    if case_id is not None:
        filtered = filtered.filter(Defect.case_id == case_id)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filtered = filtered.filter(Defect.title.like(pat))
    total = filtered.count()
    rows = (
        filtered.options(selectinload(Defect.attachments))
        .order_by(Defect.updated_at.desc(), Defect.id.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )
    titles = _case_title_map(db, project_id, {r.case_id for r in rows if r.case_id})
    return Page[DefectOut](
        items=[_defect_to_out(r, titles) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/", response_model=DefectOut, status_code=201)
def create_defect(
    project_id: int,
    body: DefectCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> DefectOut:
    _validate_refs(
        db,
        project_id,
        case_id=body.case_id,
        requirement_id=body.requirement_id,
        cycle_item_id=body.cycle_item_id,
    )
    _ensure_member(db, project_id, body.assignee_id)
    row = Defect(
        project_id=project_id,
        title=body.title.strip(),
        description=body.description or "",
        severity=body.severity,
        status=body.status,
        case_id=body.case_id,
        cycle_item_id=body.cycle_item_id,
        requirement_id=body.requirement_id,
        reporter_id=user.id,
        assignee_id=body.assignee_id,
    )
    db.add(row)
    db.commit()
    return _out_one(db, project_id, _require_defect(db, project_id, row.id))


@router.post("/from-cycle-item/{item_id}", response_model=DefectOut, status_code=201)
def create_defect_from_cycle_item(
    project_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> DefectOut:
    item = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycleItem.id == item_id, TestPlan.project_id == project_id)
        .options(selectinload(TestCycleItem.attachments))
        .first()
    )
    if item is None:
        raise AppError("not_found", TEST_CYCLE_ITEM_NOT_FOUND, status_code=404)
    if item.result != "failed":
        raise AppError("bad_request", DEFECT_FROM_ITEM_REQUIRES_FAILED, status_code=400)

    case = db.get(TestCase, item.case_id)
    cycle = db.get(TestCycle, item.cycle_id)
    plan = db.get(TestPlan, cycle.plan_id) if cycle else None
    case_title = case.title if case else f"用例#{item.case_id}"
    plan_name = plan.name if plan else ""
    cycle_name = cycle.name if cycle else ""
    version = plan.version if plan and plan.version else ""
    iteration = plan.iteration if plan and plan.iteration else ""

    desc_parts = [
        f"来源计划：{plan_name}",
        f"轮次：{cycle_name}",
    ]
    if version:
        desc_parts.append(f"版本：{version}")
    if iteration:
        desc_parts.append(f"迭代：{iteration}")
    desc_parts.append(f"用例：{case_title} (#{item.case_id})")
    if item.actual_result:
        desc_parts.append(f"实际结果：\n{item.actual_result}")

    row = Defect(
        project_id=project_id,
        title=f"[失败] {case_title}",
        description="\n".join(desc_parts),
        severity=case.priority if case and case.priority else "P2",
        status="open",
        case_id=item.case_id,
        cycle_item_id=item.id,
        requirement_id=case.requirement_id if case else None,
        reporter_id=user.id,
        assignee_id=item.assignee_id,
    )
    db.add(row)
    db.flush()
    _copy_execution_attachments(
        db,
        project_id=project_id,
        defect=row,
        cycle_item_id=item.id,
        uploader_id=user.id,
    )
    db.commit()
    return _out_one(db, project_id, _require_defect(db, project_id, row.id))


@router.get("/{defect_id}", response_model=DefectOut)
def get_defect(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> DefectOut:
    return _out_one(db, project_id, _require_defect(db, project_id, defect_id))


@router.patch("/{defect_id}", response_model=DefectOut)
def update_defect(
    project_id: int,
    defect_id: int,
    body: DefectUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> DefectOut:
    row = _require_defect(db, project_id, defect_id)
    data = body.model_dump(exclude_unset=True)
    if "assignee_id" in data:
        _ensure_member(db, project_id, data["assignee_id"])
    _validate_refs(
        db,
        project_id,
        case_id=data.get("case_id"),
        requirement_id=data.get("requirement_id"),
        cycle_item_id=data.get("cycle_item_id"),
    )
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    return _out_one(db, project_id, _require_defect(db, project_id, defect_id))


@router.delete("/{defect_id}", status_code=204)
def delete_defect(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_defect(db, project_id, defect_id)
    for att in list(row.attachments):
        remove_storage_key(att.storage_key)
    db.delete(row)
    db.commit()


@router.get("/{defect_id}/attachments", response_model=list[DefectAttachmentOut])
def list_attachments(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[DefectAttachmentOut]:
    row = _require_defect(db, project_id, defect_id)
    return [DefectAttachmentOut.model_validate(a) for a in row.attachments]


@router.post("/{defect_id}/attachments", response_model=DefectAttachmentOut, status_code=201)
async def upload_attachment(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
) -> DefectAttachmentOut:
    row = _require_defect(db, project_id, defect_id)
    data = await file.read()
    filename = file.filename or "attachment.bin"
    mime = file.content_type or "application/octet-stream"
    _validate_upload(filename, mime, len(data))
    storage_key, size = save_defect_attachment(project_id, row.id, filename, data)
    att = DefectAttachment(
        defect_id=row.id,
        filename=filename,
        mime=mime,
        storage_key=storage_key,
        size_bytes=size,
        uploader_id=user.id,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return DefectAttachmentOut.model_validate(att)


@router.get("/{defect_id}/attachments/{attachment_id}/file")
def download_attachment(
    project_id: int,
    defect_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> FileResponse:
    _require_defect(db, project_id, defect_id)
    att = (
        db.query(DefectAttachment)
        .filter(DefectAttachment.id == attachment_id, DefectAttachment.defect_id == defect_id)
        .first()
    )
    if att is None:
        raise AppError("not_found", DEFECT_ATTACHMENT_NOT_FOUND, status_code=404)
    if not att.storage_key.startswith(f"{project_id}/"):
        raise AppError("not_found", DEFECT_ATTACHMENT_NOT_FOUND, status_code=404)
    path = absolute_path_for_storage_key(att.storage_key)
    if not path.is_file():
        raise AppError("not_found", DEFECT_ATTACHMENT_NOT_FOUND, status_code=404)
    return FileResponse(path, media_type=att.mime, filename=att.filename)


@router.delete("/{defect_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    project_id: int,
    defect_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> None:
    _require_defect(db, project_id, defect_id)
    att = (
        db.query(DefectAttachment)
        .filter(DefectAttachment.id == attachment_id, DefectAttachment.defect_id == defect_id)
        .first()
    )
    if att is None:
        raise AppError("not_found", DEFECT_ATTACHMENT_NOT_FOUND, status_code=404)
    remove_storage_key(att.storage_key)
    db.delete(att)
    db.commit()
