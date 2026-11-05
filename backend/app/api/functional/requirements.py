from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.functional.requirement import Requirement, RequirementAttachment, RequirementVersion
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.functional.requirement import (
    AnalysisChatIn,
    AnalysisChatOut,
    AnalysisSaveIn,
    RequirementAttachmentOut,
    RequirementCreate,
    RequirementImportBodyOut,
    RequirementOut,
    RequirementUpdate,
    RequirementVersionOut,
)
from app.schemas.functional.test_plan import (
    TestPlanDraftAccept,
    TestPlanDraftChatIn,
    TestPlanDraftOut,
    TestPlanOut,
)
from app.services.functional.asset_versions import (
    ensure_baseline_requirement_version,
    snapshot_requirement_version,
)
from app.services.functional.requirement_analysis import (
    accept_requirement_analysis,
    analyze_requirement,
    chat_requirement_analysis,
    discard_requirement_analysis,
    save_requirement_analysis,
)
from app.services.functional.requirement_content import recompute_extracted_text
from app.services.functional.test_plan_draft import (
    accept_test_plan_draft,
    chat_test_plan_draft,
    generate_test_plan_draft,
    plan_to_out,
)
from app.services.knowledge.document_extract import (
    MAX_ATTACHMENT_BYTES,
    extract_requirement_attachment_text,
    extract_text_from_bytes,
    mime_for_requirement_attachment,
    requirement_attachment_allowed_suffixes,
)
from app.services.file_storage import (
    absolute_path_for_storage_key,
    remove_storage_key,
    save_requirement_attachment,
)
from app.tasks.knowledge.reindex_entity import enqueue_reindex
from app.user_messages import (
    IMPORT_FAILED,
    REQUIREMENT_ATTACHMENT_NOT_FOUND,
    REQUIREMENT_ATTACHMENT_TYPE_INVALID,
    REQUIREMENT_BODY_OR_FILE_REQUIRED,
    REQUIREMENT_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["requirements"])

_ALLOWED_EXT = requirement_attachment_allowed_suffixes()


def _get_requirement(db: Session, project_id: int, req_id: int) -> Requirement:
    r = (
        db.query(Requirement)
        .filter(Requirement.id == req_id, Requirement.project_id == project_id)
        .options(selectinload(Requirement.attachments))
        .first()
    )
    if r is None:
        raise AppError("not_found", REQUIREMENT_NOT_FOUND, status_code=404)
    return r


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    p = max(1, page)
    ps = min(100, max(1, page_size))
    return p, ps


def _out(r: Requirement) -> RequirementOut:
    return RequirementOut.model_validate(r)


def _validate_attachment_upload(filename: str, size: int) -> None:
    suf = Path(filename or "").suffix.lower()
    if suf not in _ALLOWED_EXT:
        raise AppError("bad_request", REQUIREMENT_ATTACHMENT_TYPE_INVALID, status_code=400)
    if size <= 0 or size > MAX_ATTACHMENT_BYTES:
        raise AppError(
            "bad_request",
            f"附件大小需在 1 字节 ~ {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 之间",
            status_code=400,
        )


def _add_attachment(
    db: Session,
    req: Requirement,
    *,
    filename: str,
    data: bytes,
    mime_hint: str | None,
    uploader_id: int | None,
) -> RequirementAttachment:
    safe_name = Path(filename).name or "attachment.bin"
    _validate_attachment_upload(safe_name, len(data))
    try:
        extract_requirement_attachment_text(safe_name, data)
    except ValueError as e:
        raise AppError("bad_request", f"{IMPORT_FAILED}：{e}", status_code=400) from e
    mime = mime_for_requirement_attachment(safe_name, mime_hint)
    storage_key, size = save_requirement_attachment(req.project_id, req.id, safe_name, data)
    att = RequirementAttachment(
        requirement_id=req.id,
        filename=safe_name,
        mime=mime,
        storage_key=storage_key,
        size_bytes=size,
        uploader_id=uploader_id,
    )
    db.add(att)
    db.flush()
    req.body_md = ""
    if att not in (req.attachments or []):
        req.attachments.append(att)
    recompute_extracted_text(req)
    return att


async def _create_from_upload_files(
    *,
    project_id: int,
    files: list[UploadFile],
    title: str | None,
    status: str,
    priority: str | None,
    db: Session,
    user: User,
) -> RequirementOut:
    payloads: list[tuple[str, bytes, str | None]] = []
    for file in files:
        raw_name = file.filename or "document"
        data = await file.read()
        if not data:
            continue
        payloads.append((raw_name, data, file.content_type))
    if not payloads:
        raise AppError("bad_request", REQUIREMENT_BODY_OR_FILE_REQUIRED, status_code=400)
    first_name = payloads[0][0]
    display_title = (title or "").strip() or Path(first_name).stem or "未命名需求"
    req = Requirement(
        project_id=project_id,
        title=display_title[:512],
        body_md="",
        status=status or "draft",
        priority=priority or None,
        creator_id=user.id,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    try:
        for raw_name, data, mime_hint in payloads:
            _add_attachment(
                db,
                req,
                filename=raw_name,
                data=data,
                mime_hint=mime_hint,
                uploader_id=user.id,
            )
    except AppError:
        for att in list(req.attachments or []):
            remove_storage_key(att.storage_key)
        db.delete(req)
        db.commit()
        raise
    db.commit()
    enqueue_reindex(project_id, "requirement", req.id)
    return _out(_get_requirement(db, project_id, req.id))


@router.get("/", response_model=Page[RequirementOut])
def list_requirements(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
) -> Page[RequirementOut]:
    p, ps = _clamp_pagination(page, page_size)
    q = (
        db.query(Requirement)
        .filter(Requirement.project_id == project_id)
        .options(selectinload(Requirement.attachments))
    )
    if search and search.strip():
        pat = f"%{search.strip()}%"
        q = q.outerjoin(RequirementAttachment).filter(
            or_(
                Requirement.title.like(pat),
                Requirement.body_md.like(pat),
                Requirement.extracted_text.like(pat),
                RequirementAttachment.filename.like(pat),
            )
        ).distinct()
    total = q.count()
    rows = q.order_by(Requirement.updated_at.desc(), Requirement.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[RequirementOut](
        items=[_out(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/import-body", response_model=RequirementImportBodyOut)
async def import_requirement_body(
    project_id: int,
    file: UploadFile = File(...),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> RequirementImportBodyOut:
    raw_name = file.filename or "document"
    data = await file.read()
    try:
        text = extract_text_from_bytes(raw_name, data)
    except ValueError as e:
        raise AppError("bad_request", f"{IMPORT_FAILED}：{e}", status_code=400) from e
    return RequirementImportBodyOut(body_md=text)


@router.post("/", response_model=RequirementOut, status_code=201)
def create_requirement(
    project_id: int,
    body: RequirementCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> RequirementOut:
    req = Requirement(
        project_id=project_id,
        title=body.title,
        body_md=body.body_md,
        status=body.status,
        priority=body.priority,
        acceptance_rules=body.acceptance_rules or "",
        creator_id=user.id,
    )
    db.add(req)
    db.flush()
    ensure_baseline_requirement_version(db, req, source="create", actor_id=user.id)
    db.commit()
    db.refresh(req)
    enqueue_reindex(project_id, "requirement", req.id)
    return _out(req)


@router.post("/from-files", response_model=RequirementOut, status_code=201)
async def create_requirement_from_files(
    project_id: int,
    files: list[UploadFile] = File(...),
    title: str | None = Form(None),
    status: str = Form("draft"),
    priority: str | None = Form(None),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> RequirementOut:
    return await _create_from_upload_files(
        project_id=project_id,
        files=files,
        title=title,
        status=status,
        priority=priority,
        db=db,
        user=user,
    )


# Back-compat alias for single-file clients.
@router.post("/from-file", response_model=RequirementOut, status_code=201)
async def create_requirement_from_file(
    project_id: int,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    status: str = Form("draft"),
    priority: str | None = Form(None),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> RequirementOut:
    return await _create_from_upload_files(
        project_id=project_id,
        files=[file],
        title=title,
        status=status,
        priority=priority,
        db=db,
        user=user,
    )


@router.post("/{requirement_id}/analyze", response_model=RequirementOut)
def analyze_requirement_endpoint(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> RequirementOut:
    req = _get_requirement(db, project_id, requirement_id)
    return _out(analyze_requirement(db, req))


@router.post("/{requirement_id}/analyze/chat", response_model=AnalysisChatOut)
def chat_requirement_analysis_endpoint(
    project_id: int,
    requirement_id: int,
    body: AnalysisChatIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AnalysisChatOut:
    req = _get_requirement(db, project_id, requirement_id)
    return chat_requirement_analysis(db, req, body)


@router.post("/{requirement_id}/analyze/accept", response_model=AnalysisChatOut)
def accept_requirement_analysis_endpoint(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AnalysisChatOut:
    req = _get_requirement(db, project_id, requirement_id)
    return accept_requirement_analysis(db, req)


@router.post("/{requirement_id}/analyze/discard", response_model=AnalysisChatOut)
def discard_requirement_analysis_endpoint(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AnalysisChatOut:
    req = _get_requirement(db, project_id, requirement_id)
    return discard_requirement_analysis(db, req)


@router.post("/{requirement_id}/analyze/save", response_model=AnalysisChatOut)
def save_requirement_analysis_endpoint(
    project_id: int,
    requirement_id: int,
    body: AnalysisSaveIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AnalysisChatOut:
    req = _get_requirement(db, project_id, requirement_id)
    return save_requirement_analysis(db, req, body)


@router.post("/{requirement_id}/test-plan-draft", response_model=TestPlanDraftOut)
def generate_requirement_test_plan_draft(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> TestPlanDraftOut:
    req = _get_requirement(db, project_id, requirement_id)
    return generate_test_plan_draft(db, req)


@router.post("/{requirement_id}/test-plan-draft/chat", response_model=TestPlanDraftOut)
def chat_requirement_test_plan_draft(
    project_id: int,
    requirement_id: int,
    body: TestPlanDraftChatIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> TestPlanDraftOut:
    req = _get_requirement(db, project_id, requirement_id)
    return chat_test_plan_draft(db, req, body)


@router.post("/{requirement_id}/test-plan-draft/accept", response_model=TestPlanOut)
def accept_requirement_test_plan_draft(
    project_id: int,
    requirement_id: int,
    body: TestPlanDraftAccept,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestPlanOut:
    req = _get_requirement(db, project_id, requirement_id)
    row = accept_test_plan_draft(db, req, body, user)
    return plan_to_out(db, row)


@router.get("/{requirement_id}", response_model=RequirementOut)
def get_requirement(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> RequirementOut:
    return _out(_get_requirement(db, project_id, requirement_id))


@router.patch("/{requirement_id}", response_model=RequirementOut)
def update_requirement(
    project_id: int,
    requirement_id: int,
    body: RequirementUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> RequirementOut:
    req = _get_requirement(db, project_id, requirement_id)
    data = body.model_dump(exclude_unset=True)
    change_note = data.pop("change_note", None)
    if req.attachments and "body_md" in data:
        # Attachment-backed requirements do not edit Markdown body.
        data.pop("body_md", None)
    if "acceptance_rules" in data and data["acceptance_rules"] is not None:
        data["acceptance_rules"] = data["acceptance_rules"].strip()
    content_changed = bool(data)
    for key, val in data.items():
        setattr(req, key, val)
    if not req.attachments and not (req.body_md or "").strip():
        raise AppError("bad_request", REQUIREMENT_BODY_OR_FILE_REQUIRED, status_code=400)
    if content_changed:
        # Keeps old plans/runs pointing at the text they were built from.
        snapshot_requirement_version(
            db, req, source="edit", note=change_note, actor_id=user.id
        )
    db.commit()
    db.refresh(req)
    enqueue_reindex(project_id, "requirement", req.id)
    return _out(req)


@router.get("/{requirement_id}/versions", response_model=list[RequirementVersionOut])
def list_requirement_versions(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[RequirementVersionOut]:
    req = _get_requirement(db, project_id, requirement_id)
    ensure_baseline_requirement_version(db, req, source="migration")
    db.commit()
    rows = (
        db.query(RequirementVersion)
        .filter(RequirementVersion.requirement_id == req.id)
        .order_by(RequirementVersion.version_no.desc(), RequirementVersion.id.desc())
        .all()
    )
    return [RequirementVersionOut.model_validate(r) for r in rows]


@router.get("/{requirement_id}/attachments", response_model=list[RequirementAttachmentOut])
def list_attachments(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[RequirementAttachmentOut]:
    req = _get_requirement(db, project_id, requirement_id)
    return [RequirementAttachmentOut.model_validate(a) for a in req.attachments]


@router.post(
    "/{requirement_id}/attachments",
    response_model=RequirementAttachmentOut,
    status_code=201,
)
async def upload_attachment(
    project_id: int,
    requirement_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> RequirementAttachmentOut:
    req = _get_requirement(db, project_id, requirement_id)
    raw_name = file.filename or "attachment.bin"
    data = await file.read()
    if not data:
        raise AppError("bad_request", REQUIREMENT_ATTACHMENT_TYPE_INVALID, status_code=400)
    att = _add_attachment(
        db,
        req,
        filename=raw_name,
        data=data,
        mime_hint=file.content_type,
        uploader_id=user.id,
    )
    db.commit()
    db.refresh(att)
    enqueue_reindex(project_id, "requirement", req.id)
    return RequirementAttachmentOut.model_validate(att)


@router.get("/{requirement_id}/attachments/{attachment_id}/file")
def download_attachment(
    project_id: int,
    requirement_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> FileResponse:
    _get_requirement(db, project_id, requirement_id)
    att = (
        db.query(RequirementAttachment)
        .filter(
            RequirementAttachment.id == attachment_id,
            RequirementAttachment.requirement_id == requirement_id,
        )
        .first()
    )
    if att is None:
        raise AppError("not_found", REQUIREMENT_ATTACHMENT_NOT_FOUND, status_code=404)
    if not att.storage_key.startswith(f"{project_id}/"):
        raise AppError("not_found", REQUIREMENT_ATTACHMENT_NOT_FOUND, status_code=404)
    path = absolute_path_for_storage_key(att.storage_key)
    if not path.is_file():
        raise AppError("not_found", REQUIREMENT_ATTACHMENT_NOT_FOUND, status_code=404)
    return FileResponse(
        path,
        media_type=att.mime,
        filename=att.filename,
        content_disposition_type="inline",
    )


@router.delete("/{requirement_id}/attachments/{attachment_id}", response_model=RequirementOut)
def delete_attachment(
    project_id: int,
    requirement_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> RequirementOut:
    req = _get_requirement(db, project_id, requirement_id)
    att = (
        db.query(RequirementAttachment)
        .filter(
            RequirementAttachment.id == attachment_id,
            RequirementAttachment.requirement_id == requirement_id,
        )
        .first()
    )
    if att is None:
        raise AppError("not_found", REQUIREMENT_ATTACHMENT_NOT_FOUND, status_code=404)
    remove_storage_key(att.storage_key)
    db.delete(att)
    db.flush()
    db.refresh(req, attribute_names=["attachments"])
    recompute_extracted_text(req)
    if not req.attachments and not (req.body_md or "").strip():
        req.body_md = "(已移除附件，请补充正文)"
    db.commit()
    db.refresh(req)
    enqueue_reindex(project_id, "requirement", req.id)
    return _out(req)


@router.delete("/{requirement_id}", status_code=204)
def delete_requirement(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    req = _get_requirement(db, project_id, requirement_id)
    for att in list(req.attachments or []):
        remove_storage_key(att.storage_key)
    db.delete(req)
    db.commit()
