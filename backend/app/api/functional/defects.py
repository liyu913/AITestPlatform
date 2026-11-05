from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.defect import (
    DEFECT_CATEGORIES,
    DEFECT_STATUSES,
    Defect,
    DefectAttachment,
    DefectStatusHistory,
)
from app.models.api_automation.api_automation import ApiCase, ApiSuite
from app.models.ui_automation.ui_automation import UiCase, UiSuite
from app.models.project.project import ProjectMember
from app.models.functional.requirement import Requirement
from app.models.functional.testcase import TestCase
from app.models.functional.test_plan import (
    ExecutionAttachment,
    TestCycle,
    TestCycleAttempt,
    TestCycleItem,
    TestPlan,
)
from app.models.identity.user import User
from app.schemas.functional.defect import (
    DefectAttachmentOut,
    DefectCreate,
    DefectEvidenceAttempt,
    DefectEvidenceCycle,
    DefectEvidenceItem,
    DefectEvidenceOut,
    DefectHistoryOut,
    DefectOut,
    DefectRegressionIn,
    DefectSuggestionOut,
    DefectUpdate,
    DefectVerifyIn,
)
from app.schemas.page import Page
from app.services.file_storage import (
    absolute_path_for_storage_key,
    remove_storage_key,
    save_defect_attachment,
)
from app.services.functional.defect_workflow import (
    apply_verification,
    change_status,
    close_blockers,
    dedupe_existing,
    link_regression_cycles,
    record_history,
    suggestion_from_cycle_item,
)
from app.user_messages import (
    CYCLE_ASSIGNEE_NOT_MEMBER,
    DEFECT_ATTACHMENT_NOT_FOUND,
    DEFECT_ATTACHMENT_TOO_LARGE,
    DEFECT_ATTACHMENT_TYPE_INVALID,
    DEFECT_DUPLICATE_SOURCE,
    DEFECT_FROM_ITEM_REQUIRES_FAILED,
    DEFECT_NOT_FOUND,
    DEFECT_STATUS_INVALID,
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


def _require_cycle_item(
    db: Session, project_id: int, item_id: int, *, with_attachments: bool = False
) -> TestCycleItem:
    q = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycleItem.id == item_id, TestPlan.project_id == project_id)
    )
    if with_attachments:
        q = q.options(selectinload(TestCycleItem.attachments))
    item = q.first()
    if item is None:
        raise AppError("not_found", TEST_CYCLE_ITEM_NOT_FOUND, status_code=404)
    return item


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
    regression_ids = (
        [int(x) for x in row.regression_cycle_ids_json]
        if isinstance(row.regression_cycle_ids_json, list)
        else []
    )
    return DefectOut(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        description=row.description or "",
        severity=row.severity,
        status=row.status,
        category=row.category or "product",
        case_id=row.case_id,
        case_title=case_title,
        cycle_item_id=row.cycle_item_id,
        requirement_id=row.requirement_id,
        failure_source_type=row.failure_source_type,
        failure_source_id=row.failure_source_id,
        expected_result=row.expected_result or "",
        actual_result=row.actual_result or "",
        environment=row.environment,
        found_build=row.found_build,
        fix_build=row.fix_build,
        verified_at=row.verified_at,
        verified_by_id=row.verified_by_id,
        verification_result=row.verification_result,
        verification_note=row.verification_note or "",
        regression_cycle_ids=regression_ids,
        closed_at=row.closed_at,
        closed_by_id=row.closed_by_id,
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
    category: str | None = Query(None, max_length=32),
    search: str | None = Query(None, max_length=256),
    case_id: int | None = Query(None, ge=1),
) -> Page[DefectOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(Defect).filter(Defect.project_id == project_id)
    if status:
        filtered = filtered.filter(Defect.status == status)
    if category:
        filtered = filtered.filter(Defect.category == category)
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
    existing = dedupe_existing(db, project_id, body.failure_source_type, body.failure_source_id)
    if existing is not None:
        raise AppError(
            "conflict",
            DEFECT_DUPLICATE_SOURCE.format(defect_id=existing.id),
            status_code=409,
        )
    row = Defect(
        project_id=project_id,
        title=body.title.strip(),
        description=body.description or "",
        severity=body.severity,
        status=body.status,
        category=body.category,
        case_id=body.case_id,
        cycle_item_id=body.cycle_item_id,
        requirement_id=body.requirement_id,
        failure_source_type=body.failure_source_type,
        failure_source_id=body.failure_source_id,
        expected_result=body.expected_result or "",
        actual_result=body.actual_result or "",
        environment=(body.environment or "").strip() or None,
        found_build=(body.found_build or "").strip() or None,
        reporter_id=user.id,
        assignee_id=body.assignee_id,
    )
    db.add(row)
    db.flush()
    record_history(
        db,
        row,
        from_status="",
        to_status=row.status,
        comment="创建缺陷",
        actor_id=user.id,
    )
    db.commit()
    return _out_one(db, project_id, _require_defect(db, project_id, row.id))


@router.get("/suggestion/from-cycle-item/{item_id}", response_model=DefectSuggestionOut)
def suggest_defect_from_cycle_item(
    project_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> DefectSuggestionOut:
    """Editable draft built from the failure: category, expected/actual, build."""
    item = _require_cycle_item(db, project_id, item_id)
    return suggestion_from_cycle_item(db, project_id, item)


@router.post("/from-cycle-item/{item_id}", response_model=DefectOut, status_code=201)
def create_defect_from_cycle_item(
    project_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> DefectOut:
    item = _require_cycle_item(db, project_id, item_id, with_attachments=True)
    if item.result != "failed":
        raise AppError("bad_request", DEFECT_FROM_ITEM_REQUIRES_FAILED, status_code=400)

    # One defect per failed attempt: repeated clicks point at the existing record.
    existing = dedupe_existing(db, project_id, "cycle_item", item.id)
    if existing is not None:
        raise AppError(
            "conflict",
            DEFECT_DUPLICATE_SOURCE.format(defect_id=existing.id),
            status_code=409,
        )

    case = db.get(TestCase, item.case_id) if item.case_id is not None else None
    cycle = db.get(TestCycle, item.cycle_id)
    plan = db.get(TestPlan, cycle.plan_id) if cycle else None
    suggestion = suggestion_from_cycle_item(db, project_id, item)
    case_title = case.title if case else f"执行项#{item.id}"
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
    if suggestion.expected_result:
        desc_parts.append(f"预期结果：\n{suggestion.expected_result}")
    if item.actual_result:
        desc_parts.append(f"实际结果：\n{item.actual_result}")
    if suggestion.environment:
        desc_parts.append(f"环境：{suggestion.environment}")
    if suggestion.found_build:
        desc_parts.append(f"发现构建：{suggestion.found_build}")

    row = Defect(
        project_id=project_id,
        title=f"[失败] {case_title}",
        description="\n".join(desc_parts),
        severity=case.priority if case and case.priority else "P2",
        status="open",
        category=suggestion.category,
        case_id=item.case_id,
        cycle_item_id=item.id,
        requirement_id=case.requirement_id if case else None,
        failure_source_type="cycle_item",
        failure_source_id=item.id,
        expected_result=suggestion.expected_result,
        actual_result=item.actual_result or "",
        environment=suggestion.environment,
        found_build=suggestion.found_build,
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
    record_history(
        db,
        row,
        from_status="",
        to_status="open",
        comment=f"从执行项 #{item.id} 创建（分类建议：{suggestion.category}）",
        actor_id=user.id,
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
    user: User = Depends(get_current_user),
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
    if "category" in data and data["category"] is not None:
        if data["category"] not in DEFECT_CATEGORIES:
            raise AppError("bad_request", "无效的失败分类", status_code=400)
    new_status = data.pop("status", None)
    comment = data.pop("status_comment", None)
    for k, v in data.items():
        setattr(row, k, v)
    if new_status is not None:
        if new_status not in DEFECT_STATUSES:
            raise AppError("bad_request", DEFECT_STATUS_INVALID, status_code=400)
        change_status(
            db,
            project_id,
            row,
            to_status=new_status,
            comment=comment,
            actor_id=user.id,
        )
    db.commit()
    return _out_one(db, project_id, _require_defect(db, project_id, defect_id))


@router.post("/{defect_id}/verify", response_model=DefectOut)
def verify_defect(
    project_id: int,
    defect_id: int,
    body: DefectVerifyIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> DefectOut:
    """Record the retest of the original problem against the fix build."""
    row = _require_defect(db, project_id, defect_id)
    if body.cycle_id is not None:
        _require_cycle_item_cycle(db, project_id, body.cycle_id)
    apply_verification(
        db,
        row,
        result=body.result,
        fix_build=body.fix_build,
        note=body.note,
        actor_id=user.id,
    )
    if body.cycle_id is not None:
        current = (
            [int(x) for x in row.regression_cycle_ids_json]
            if isinstance(row.regression_cycle_ids_json, list)
            else []
        )
        if body.cycle_id not in current:
            row.regression_cycle_ids_json = [*current, body.cycle_id]
    db.commit()
    return _out_one(db, project_id, _require_defect(db, project_id, defect_id))


def _require_cycle_item_cycle(db: Session, project_id: int, cycle_id: int) -> TestCycle:
    cycle = (
        db.query(TestCycle)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycle.id == cycle_id, TestPlan.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise AppError("bad_request", "回归批次不存在或不属于当前项目", status_code=400)
    return cycle


@router.post("/{defect_id}/regression-cycles", response_model=DefectOut)
def set_regression_cycles(
    project_id: int,
    defect_id: int,
    body: DefectRegressionIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> DefectOut:
    row = _require_defect(db, project_id, defect_id)
    linked = link_regression_cycles(db, project_id, row, body.cycle_ids)
    db.commit()
    out = _out_one(db, project_id, _require_defect(db, project_id, defect_id))
    out.regression_cycle_ids = linked
    return out


@router.get("/{defect_id}/close-blockers", response_model=list[str])
def list_close_blockers(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[str]:
    """What still prevents closing this defect (empty list = closable)."""
    row = _require_defect(db, project_id, defect_id)
    return close_blockers(db, project_id, row)


@router.get("/{defect_id}/evidence", response_model=DefectEvidenceOut)
def get_defect_evidence(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> DefectEvidenceOut:
    """Original failure evidence plus every attempt and status change."""
    row = _require_defect(db, project_id, defect_id)
    item: TestCycleItem | None = None
    cycle: TestCycle | None = None
    if row.cycle_item_id is not None:
        item = (
            db.query(TestCycleItem)
            .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
            .join(TestPlan, TestCycle.plan_id == TestPlan.id)
            .filter(TestCycleItem.id == row.cycle_item_id, TestPlan.project_id == project_id)
            .first()
        )
        if item is not None:
            cycle = db.get(TestCycle, item.cycle_id)

    plan = db.get(TestPlan, cycle.plan_id) if cycle is not None else None
    env_name = None
    if cycle is not None and isinstance(cycle.env_snapshot_json, dict):
        env_name = str(cycle.env_snapshot_json.get("name") or "") or None

    attempts = (
        db.query(TestCycleAttempt)
        .filter(TestCycleAttempt.cycle_item_id == item.id)
        .order_by(TestCycleAttempt.attempt_no.asc())
        .all()
        if item is not None
        else []
    )
    history = (
        db.query(DefectStatusHistory)
        .filter(DefectStatusHistory.defect_id == row.id)
        .order_by(DefectStatusHistory.id.asc())
        .all()
    )

    return DefectEvidenceOut(
        defect_id=row.id,
        cycle=(
            DefectEvidenceCycle(
                id=cycle.id if cycle else None,
                name=cycle.name if cycle else None,
                plan_name=plan.name if plan else None,
                target_build=cycle.target_build if cycle else None,
                environment=env_name,
            )
            if (cycle is not None or item is not None)
            else None
        ),
        item=(
            DefectEvidenceItem(
                id=item.id,
                asset_type=item.asset_type or "functional",
                asset_title=_asset_title(db, item),
                result=item.result,
                actual_result=item.actual_result or "",
                executed_at=item.executed_at,
                attempt_no=item.attempt_no or 0,
            )
            if item is not None
            else None
        ),
        attempts=[
            DefectEvidenceAttempt(
                id=a.id,
                attempt_no=a.attempt_no,
                execution_type=a.execution_type,
                status=a.status,
                summary=a.summary or "",
                original_run_type=a.original_run_type,
                original_run_id=a.original_run_id,
                started_at=a.started_at,
                finished_at=a.finished_at,
            )
            for a in attempts
        ],
        verification_result=row.verification_result,
        verification_note=row.verification_note or "",
        verified_at=row.verified_at,
        history=[DefectHistoryOut.model_validate(h) for h in history],
    )


def _asset_title(db: Session, item: TestCycleItem) -> str | None:
    if item.case_id is not None:
        case = db.get(TestCase, item.case_id)
        return case.title if case is not None else None
    asset_id = item.asset_id
    if asset_id is None:
        return None
    models = {
        "api_case": ApiCase,
        "api_suite": ApiSuite,
        "ui_case": UiCase,
        "ui_suite": UiSuite,
    }
    model = models.get(item.asset_type or "")
    if model is None:
        return None
    found = db.get(model, asset_id)
    return getattr(found, "name", None) if found is not None else None


@router.get("/{defect_id}/history", response_model=list[DefectHistoryOut])
def list_defect_history(
    project_id: int,
    defect_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[DefectHistoryOut]:
    _require_defect(db, project_id, defect_id)
    rows = (
        db.query(DefectStatusHistory)
        .filter(DefectStatusHistory.defect_id == defect_id)
        .order_by(DefectStatusHistory.id.asc())
        .all()
    )
    return [DefectHistoryOut.model_validate(r) for r in rows]


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
