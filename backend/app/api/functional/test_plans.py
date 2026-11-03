from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload

from app.core.datetime_json import now_beijing
from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.functional.test_plan import ExecutionAttachment, TestCycle, TestCycleItem, TestPlan
from app.services.functional.test_plan_draft import plan_to_out, requirement_titles
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.functional.test_plan import (
    CycleItemOut,
    CycleItemsAdd,
    CycleItemsAssign,
    CycleItemUpdate,
    ExecutionAttachmentOut,
    TestCycleCreate,
    TestCycleOut,
    TestCycleUpdate,
    TestPlanCreate,
    TestPlanOut,
    TestPlanUpdate,
)
from app.services.file_storage import (
    absolute_path_for_storage_key,
    remove_storage_key,
    save_execution_attachment,
)
from app.user_messages import (
    CYCLE_ASSIGNEE_NOT_MEMBER,
    EXECUTION_ATTACHMENT_NOT_FOUND,
    EXECUTION_ATTACHMENT_TOO_LARGE,
    EXECUTION_ATTACHMENT_TYPE_INVALID,
    TEST_CASE_NOT_FOUND,
    TEST_CYCLE_ITEM_NOT_FOUND,
    TEST_CYCLE_NOT_FOUND,
    TEST_PLAN_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["test-plans"])

_ALLOWED_ATTACHMENT_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
_RESULT_SYNC_STATUSES = {"passed", "failed", "blocked"}
_VALID_RESULTS = {"untested", "passed", "failed", "blocked"}


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_plan(db: Session, project_id: int, plan_id: int) -> TestPlan:
    row = (
        db.query(TestPlan)
        .filter(TestPlan.id == plan_id, TestPlan.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", TEST_PLAN_NOT_FOUND, status_code=404)
    return row


def _require_cycle(db: Session, project_id: int, cycle_id: int) -> TestCycle:
    row = (
        db.query(TestCycle)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycle.id == cycle_id, TestPlan.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", TEST_CYCLE_NOT_FOUND, status_code=404)
    return row


def _require_item(db: Session, project_id: int, cycle_id: int, item_id: int) -> TestCycleItem:
    item = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(
            TestCycleItem.id == item_id,
            TestCycleItem.cycle_id == cycle_id,
            TestPlan.project_id == project_id,
        )
        .options(selectinload(TestCycleItem.attachments))
        .first()
    )
    if item is None:
        raise AppError("not_found", TEST_CYCLE_ITEM_NOT_FOUND, status_code=404)
    return item


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


def _cycle_stats(db: Session, cycle_id: int) -> dict[str, int]:
    items = db.query(TestCycleItem).filter(TestCycleItem.cycle_id == cycle_id).all()
    stats = {
        "item_count": len(items),
        "passed_count": 0,
        "failed_count": 0,
        "blocked_count": 0,
        "untested_count": 0,
    }
    for it in items:
        key = f"{it.result}_count"
        if key in stats:
            stats[key] += 1
        else:
            stats["untested_count"] += 1
    return stats


def _cycle_to_out(db: Session, cycle: TestCycle) -> TestCycleOut:
    stats = _cycle_stats(db, cycle.id)
    return TestCycleOut(
        id=cycle.id,
        plan_id=cycle.plan_id,
        name=cycle.name,
        status=cycle.status,
        creator_id=cycle.creator_id,
        created_at=cycle.created_at,
        updated_at=cycle.updated_at,
        **stats,
    )


def _item_to_out(db: Session, item: TestCycleItem) -> CycleItemOut:
    case = db.get(TestCase, item.case_id)
    atts = sorted(item.attachments, key=lambda a: a.id) if item.attachments else []
    return CycleItemOut(
        id=item.id,
        cycle_id=item.cycle_id,
        case_id=item.case_id,
        case_title=case.title if case else None,
        case_priority=case.priority if case else None,
        requirement_id=case.requirement_id if case else None,
        assignee_id=item.assignee_id,
        result=item.result,
        actual_result=item.actual_result or "",
        executed_by_id=item.executed_by_id,
        executed_at=item.executed_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        attachments=[ExecutionAttachmentOut.model_validate(a) for a in atts],
    )


# --- Plans ---


@router.get("/test-plans/", response_model=Page[TestPlanOut])
def list_plans(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
    requirement_id: int | None = Query(None),
) -> Page[TestPlanOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(TestPlan).filter(TestPlan.project_id == project_id)
    if requirement_id is not None:
        filtered = filtered.filter(TestPlan.requirement_id == requirement_id)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filtered = filtered.filter(TestPlan.name.like(pat))
    total = filtered.count()
    rows = filtered.order_by(TestPlan.updated_at.desc(), TestPlan.id.desc()).offset((p - 1) * ps).limit(ps).all()
    titles = requirement_titles(db, rows)
    return Page[TestPlanOut](
        items=[plan_to_out(db, r, titles) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/test-plans/", response_model=TestPlanOut, status_code=201)
def create_plan(
    project_id: int,
    body: TestPlanCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestPlanOut:
    row = TestPlan(
        project_id=project_id,
        name=body.name.strip(),
        version=body.version.strip() if body.version else None,
        iteration=body.iteration.strip() if body.iteration else None,
        description=body.description,
        status=body.status,
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return plan_to_out(db, row)


@router.get("/test-plans/{plan_id}", response_model=TestPlanOut)
def get_plan(
    project_id: int,
    plan_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> TestPlanOut:
    return plan_to_out(db, _require_plan(db, project_id, plan_id))


@router.patch("/test-plans/{plan_id}", response_model=TestPlanOut)
def update_plan(
    project_id: int,
    plan_id: int,
    body: TestPlanUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> TestPlanOut:
    row = _require_plan(db, project_id, plan_id)
    data = body.model_dump(exclude_unset=True)
    if "body_md" in data and data["body_md"] is not None:
        data["body_md"] = data["body_md"].strip() or None
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    if "version" in data and data["version"] is not None:
        data["version"] = data["version"].strip() or None
    if "iteration" in data and data["iteration"] is not None:
        data["iteration"] = data["iteration"].strip() or None
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return plan_to_out(db, row)


@router.delete("/test-plans/{plan_id}", status_code=204)
def delete_plan(
    project_id: int,
    plan_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_plan(db, project_id, plan_id)
    db.delete(row)
    db.commit()


# --- Cycles ---


@router.get("/test-plans/{plan_id}/cycles/", response_model=list[TestCycleOut])
def list_cycles(
    project_id: int,
    plan_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[TestCycleOut]:
    _require_plan(db, project_id, plan_id)
    rows = (
        db.query(TestCycle)
        .filter(TestCycle.plan_id == plan_id)
        .order_by(TestCycle.updated_at.desc(), TestCycle.id.desc())
        .all()
    )
    return [_cycle_to_out(db, c) for c in rows]


@router.post("/test-plans/{plan_id}/cycles/", response_model=TestCycleOut, status_code=201)
def create_cycle(
    project_id: int,
    plan_id: int,
    body: TestCycleCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCycleOut:
    _require_plan(db, project_id, plan_id)
    row = TestCycle(
        plan_id=plan_id,
        name=body.name.strip(),
        status=body.status,
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _cycle_to_out(db, row)


@router.get("/cycles/{cycle_id}", response_model=TestCycleOut)
def get_cycle(
    project_id: int,
    cycle_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> TestCycleOut:
    return _cycle_to_out(db, _require_cycle(db, project_id, cycle_id))


@router.patch("/cycles/{cycle_id}", response_model=TestCycleOut)
def update_cycle(
    project_id: int,
    cycle_id: int,
    body: TestCycleUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> TestCycleOut:
    row = _require_cycle(db, project_id, cycle_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _cycle_to_out(db, row)


@router.delete("/cycles/{cycle_id}", status_code=204)
def delete_cycle(
    project_id: int,
    cycle_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require_cycle(db, project_id, cycle_id)
    db.delete(row)
    db.commit()


# --- Cycle items ---


@router.get("/cycles/{cycle_id}/items/", response_model=Page[CycleItemOut])
def list_items(
    project_id: int,
    cycle_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    result: str | None = Query(None, max_length=64),
    assignee_id: int | None = Query(None, ge=1),
    search: str | None = Query(None, max_length=256),
) -> Page[CycleItemOut]:
    _require_cycle(db, project_id, cycle_id)
    p, ps = _clamp_pagination(page, page_size)
    filtered = (
        db.query(TestCycleItem)
        .filter(TestCycleItem.cycle_id == cycle_id)
        .options(selectinload(TestCycleItem.attachments))
    )
    if result:
        filtered = filtered.filter(TestCycleItem.result == result)
    if assignee_id is not None:
        filtered = filtered.filter(TestCycleItem.assignee_id == assignee_id)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filtered = filtered.join(TestCase, TestCycleItem.case_id == TestCase.id).filter(
            TestCase.title.like(pat)
        )
    total = filtered.count()
    rows = filtered.order_by(TestCycleItem.id).offset((p - 1) * ps).limit(ps).all()
    return Page[CycleItemOut](
        items=[_item_to_out(db, r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/cycles/{cycle_id}/items", response_model=list[CycleItemOut], status_code=201)
def add_items(
    project_id: int,
    cycle_id: int,
    body: CycleItemsAdd,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> list[CycleItemOut]:
    _require_cycle(db, project_id, cycle_id)
    existing = {
        r.case_id
        for r in db.query(TestCycleItem).filter(TestCycleItem.cycle_id == cycle_id).all()
    }
    created: list[TestCycleItem] = []
    for case_id in body.case_ids:
        if case_id in existing:
            continue
        case = (
            db.query(TestCase)
            .filter(TestCase.id == case_id, TestCase.project_id == project_id)
            .first()
        )
        if case is None:
            raise AppError("bad_request", TEST_CASE_NOT_FOUND, status_code=400)
        item = TestCycleItem(cycle_id=cycle_id, case_id=case_id, result="untested", actual_result="")
        db.add(item)
        created.append(item)
        existing.add(case_id)
    db.commit()
    out: list[CycleItemOut] = []
    for item in created:
        db.refresh(item)
        item = _require_item(db, project_id, cycle_id, item.id)
        out.append(_item_to_out(db, item))
    return out


@router.post("/cycles/{cycle_id}/items/assign", response_model=list[CycleItemOut])
def assign_items(
    project_id: int,
    cycle_id: int,
    body: CycleItemsAssign,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> list[CycleItemOut]:
    _require_cycle(db, project_id, cycle_id)
    _ensure_member(db, project_id, body.assignee_id)
    out: list[CycleItemOut] = []
    for item_id in body.item_ids:
        item = _require_item(db, project_id, cycle_id, item_id)
        item.assignee_id = body.assignee_id
        out.append(item)
    db.commit()
    return [_item_to_out(db, _require_item(db, project_id, cycle_id, i.id)) for i in out]


@router.patch("/cycles/{cycle_id}/items/{item_id}", response_model=CycleItemOut)
def update_item(
    project_id: int,
    cycle_id: int,
    item_id: int,
    body: CycleItemUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> CycleItemOut:
    item = _require_item(db, project_id, cycle_id, item_id)
    data = body.model_dump(exclude_unset=True)
    if "assignee_id" in data:
        _ensure_member(db, project_id, data["assignee_id"])
        item.assignee_id = data["assignee_id"]
    if "actual_result" in data and data["actual_result"] is not None:
        item.actual_result = data["actual_result"]
    if "result" in data and data["result"] is not None:
        result = data["result"]
        if result not in _VALID_RESULTS:
            raise AppError("bad_request", f"无效的执行结果：{result}", status_code=400)
        item.result = result
        if result in _RESULT_SYNC_STATUSES:
            item.executed_by_id = user.id
            item.executed_at = now_beijing()
            case = db.get(TestCase, item.case_id)
            if case is not None and case.project_id == project_id:
                case.status = result
        elif result == "untested":
            item.executed_by_id = None
            item.executed_at = None
    db.commit()
    return _item_to_out(db, _require_item(db, project_id, cycle_id, item_id))


@router.delete("/cycles/{cycle_id}/items/{item_id}", status_code=204)
def delete_item(
    project_id: int,
    cycle_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    item = _require_item(db, project_id, cycle_id, item_id)
    for att in list(item.attachments):
        remove_storage_key(att.storage_key)
    db.delete(item)
    db.commit()


# --- Attachments ---


@router.get(
    "/cycles/{cycle_id}/items/{item_id}/attachments",
    response_model=list[ExecutionAttachmentOut],
)
def list_attachments(
    project_id: int,
    cycle_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[ExecutionAttachmentOut]:
    item = _require_item(db, project_id, cycle_id, item_id)
    return [ExecutionAttachmentOut.model_validate(a) for a in item.attachments]


@router.post(
    "/cycles/{cycle_id}/items/{item_id}/attachments",
    response_model=ExecutionAttachmentOut,
    status_code=201,
)
async def upload_attachment(
    project_id: int,
    cycle_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
) -> ExecutionAttachmentOut:
    item = _require_item(db, project_id, cycle_id, item_id)
    data = await file.read()
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise AppError("bad_request", EXECUTION_ATTACHMENT_TOO_LARGE, status_code=400)
    mime = (file.content_type or "").lower()
    if mime not in _ALLOWED_ATTACHMENT_MIMES:
        raise AppError("bad_request", EXECUTION_ATTACHMENT_TYPE_INVALID, status_code=400)
    filename = file.filename or "screenshot.png"
    storage_key, size = save_execution_attachment(project_id, item.id, filename, data)
    att = ExecutionAttachment(
        cycle_item_id=item.id,
        filename=filename,
        mime=mime,
        storage_key=storage_key,
        size_bytes=size,
        uploader_id=user.id,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return ExecutionAttachmentOut.model_validate(att)


@router.get("/cycles/{cycle_id}/items/{item_id}/attachments/{attachment_id}/file")
def download_attachment(
    project_id: int,
    cycle_id: int,
    item_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> FileResponse:
    _require_item(db, project_id, cycle_id, item_id)
    att = (
        db.query(ExecutionAttachment)
        .filter(
            ExecutionAttachment.id == attachment_id,
            ExecutionAttachment.cycle_item_id == item_id,
        )
        .first()
    )
    if att is None:
        raise AppError("not_found", EXECUTION_ATTACHMENT_NOT_FOUND, status_code=404)
    if not att.storage_key.startswith(f"{project_id}/"):
        raise AppError("not_found", EXECUTION_ATTACHMENT_NOT_FOUND, status_code=404)
    path: Path = absolute_path_for_storage_key(att.storage_key)
    if not path.is_file():
        raise AppError("not_found", EXECUTION_ATTACHMENT_NOT_FOUND, status_code=404)
    return FileResponse(path, media_type=att.mime, filename=att.filename)


@router.delete(
    "/cycles/{cycle_id}/items/{item_id}/attachments/{attachment_id}",
    status_code=204,
)
def delete_attachment(
    project_id: int,
    cycle_id: int,
    item_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> None:
    _require_item(db, project_id, cycle_id, item_id)
    att = (
        db.query(ExecutionAttachment)
        .filter(
            ExecutionAttachment.id == attachment_id,
            ExecutionAttachment.cycle_item_id == item_id,
        )
        .first()
    )
    if att is None:
        raise AppError("not_found", EXECUTION_ATTACHMENT_NOT_FOUND, status_code=404)
    remove_storage_key(att.storage_key)
    db.delete(att)
    db.commit()
