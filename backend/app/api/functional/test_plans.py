from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.datetime_json import now_beijing
from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase, ApiEnvironment, ApiSuite
from app.models.project.project import ProjectMember
from app.models.functional.testcase import TestCase
from app.models.functional.test_plan import (
    ExecutionAttachment,
    TestCycle,
    TestCycleAttempt,
    TestCycleItem,
    TestPlan,
)
from app.models.ui_automation.ui_automation import UiCase, UiEnvironment, UiSuite
from app.services.functional.test_plan_draft import plan_to_out, requirement_titles
from app.models.identity.user import User
from app.schemas.page import Page
from app.schemas.functional.test_plan import (
    CycleAttemptCreate,
    CycleAttemptOut,
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
from app.services.functional.testcase_lifecycle import record_execution_result
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
# Non-functional assets that can be planned into a batch.
_ASSET_MODELS = {
    "api_case": ApiCase,
    "api_suite": ApiSuite,
    "ui_case": UiCase,
    "ui_suite": UiSuite,
}
_ATTEMPT_TO_ITEM_RESULT = {
    "passed": "passed",
    "failed": "failed",
    "blocked": "blocked",
    "error": "failed",
    "cancelled": "untested",
}


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
        owner_id=cycle.owner_id,
        target_build=cycle.target_build,
        env_kind=cycle.env_kind,
        env_id=cycle.env_id,
        env_snapshot_json=cycle.env_snapshot_json
        if isinstance(cycle.env_snapshot_json, dict)
        else None,
        credential_ref=cycle.credential_ref,
        data_note=cycle.data_note or "",
        created_at=cycle.created_at,
        updated_at=cycle.updated_at,
        **stats,
    )


def _asset_title(db: Session, asset_type: str, asset_id: int | None) -> str | None:
    if asset_id is None:
        return None
    if asset_type == "functional":
        case = db.get(TestCase, asset_id)
        return case.title if case else None
    model = _ASSET_MODELS.get(asset_type)
    if model is None:
        return None
    row = db.get(model, asset_id)
    return getattr(row, "name", None) if row is not None else None


def _env_snapshot(
    db: Session, project_id: int, kind: str | None, env_id: int | None
) -> dict | None:
    """Freeze what the batch will run against; reject another project's env."""
    if env_id is None:
        return None
    if kind not in ("api", "ui"):
        raise AppError("bad_request", "请指定环境类型（api 或 ui）", status_code=400)
    model = ApiEnvironment if kind == "api" else UiEnvironment
    row = db.get(model, env_id)
    if row is None or row.project_id != project_id:
        raise AppError("bad_request", "环境不属于当前项目", status_code=400)
    return {
        "kind": kind,
        "id": row.id,
        "name": row.name,
        "base_url": getattr(row, "base_url", None),
        "captured_at": now_beijing().isoformat(),
    }


def _item_to_out(db: Session, item: TestCycleItem) -> CycleItemOut:
    asset_id = item.asset_id if item.asset_id is not None else item.case_id
    case = db.get(TestCase, item.case_id) if item.case_id is not None else None
    atts = sorted(item.attachments, key=lambda a: a.id) if item.attachments else []
    return CycleItemOut(
        id=item.id,
        cycle_id=item.cycle_id,
        case_id=item.case_id,
        asset_type=item.asset_type or "functional",
        asset_id=asset_id,
        asset_version=item.asset_version,
        asset_title=_asset_title(db, item.asset_type or "functional", asset_id),
        case_title=case.title if case else None,
        case_priority=case.priority if case else None,
        requirement_id=case.requirement_id if case else None,
        assignee_id=item.assignee_id,
        result=item.result,
        actual_result=item.actual_result or "",
        executed_by_id=item.executed_by_id,
        executed_at=item.executed_at,
        last_run_type=item.last_run_type,
        last_run_id=item.last_run_id,
        attempt_no=item.attempt_no or 0,
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
    _ensure_member(db, project_id, body.owner_id)
    row = TestCycle(
        plan_id=plan_id,
        name=body.name.strip(),
        status=body.status,
        creator_id=user.id,
        owner_id=body.owner_id,
        target_build=(body.target_build or "").strip() or None,
        env_kind=body.env_kind,
        env_id=body.env_id,
        env_snapshot_json=_env_snapshot(db, project_id, body.env_kind, body.env_id),
        credential_ref=(body.credential_ref or "").strip() or None,
        data_note=(body.data_note or "").strip(),
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
    if "target_build" in data and data["target_build"] is not None:
        data["target_build"] = data["target_build"].strip() or None
    if "credential_ref" in data and data["credential_ref"] is not None:
        data["credential_ref"] = data["credential_ref"].strip() or None
    if "data_note" in data and data["data_note"] is not None:
        data["data_note"] = data["data_note"].strip()
    if "owner_id" in data:
        _ensure_member(db, project_id, data["owner_id"])
    env_changed = "env_kind" in data or "env_id" in data
    for k, v in data.items():
        setattr(row, k, v)
    if env_changed:
        # Re-freeze the environment only when the batch explicitly switches it.
        row.env_snapshot_json = _env_snapshot(db, project_id, row.env_kind, row.env_id)
        if row.env_id is None:
            row.env_kind = None
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

    # (asset_type, asset_id) pairs, legacy case_ids first for stable ordering.
    wanted: list[tuple[str, int]] = [("functional", cid) for cid in body.case_ids]
    wanted.extend((a.asset_type, a.asset_id) for a in body.assets)
    if not wanted:
        raise AppError("bad_request", "请至少选择一个用例或自动化资产", status_code=400)

    existing = {
        (r.asset_type or "functional", r.asset_id if r.asset_id is not None else r.case_id)
        for r in db.query(TestCycleItem).filter(TestCycleItem.cycle_id == cycle_id).all()
    }
    created: list[TestCycleItem] = []
    for asset_type, asset_id in wanted:
        key = (asset_type, asset_id)
        if key in existing:
            continue
        case_id: int | None = None
        asset_version: int | None = None
        if asset_type == "functional":
            case = (
                db.query(TestCase)
                .filter(TestCase.id == asset_id, TestCase.project_id == project_id)
                .first()
            )
            if case is None:
                raise AppError("bad_request", TEST_CASE_NOT_FOUND, status_code=400)
            case_id = case.id
            asset_version = max(1, int(case.version_no or 1))
        else:
            model = _ASSET_MODELS.get(asset_type)
            if model is None:
                raise AppError("bad_request", f"不支持的资产类型：{asset_type}", status_code=400)
            row = (
                db.query(model)
                .filter(model.id == asset_id, model.project_id == project_id)
                .first()
            )
            if row is None:
                raise AppError("bad_request", f"{asset_type} 资产不存在或不属于当前项目", status_code=400)
        item = TestCycleItem(
            cycle_id=cycle_id,
            case_id=case_id,
            asset_type=asset_type,
            asset_id=asset_id,
            asset_version=asset_version,
            result="untested",
            actual_result="",
        )
        db.add(item)
        created.append(item)
        existing.add(key)
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
                # Never touch lifecycle_status here: a manual result is an
                # execution projection, not an authoring state.
                record_execution_result(case, result, at=item.executed_at)
        elif result == "untested":
            item.executed_by_id = None
            item.executed_at = None
            case = db.get(TestCase, item.case_id)
            if case is not None and case.project_id == project_id:
                record_execution_result(case, "untested")
    db.commit()
    return _item_to_out(db, _require_item(db, project_id, cycle_id, item_id))


@router.post(
    "/cycles/{cycle_id}/items/{item_id}/attempts",
    response_model=CycleAttemptOut,
    status_code=201,
)
def create_attempt(
    project_id: int,
    cycle_id: int,
    item_id: int,
    body: CycleAttemptCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> CycleAttemptOut:
    """Record one execution attempt; retries append instead of overwriting."""
    item = _require_item(db, project_id, cycle_id, item_id)
    cycle = _require_cycle(db, project_id, cycle_id)
    last_no = (
        db.query(func.max(TestCycleAttempt.attempt_no))
        .filter(TestCycleAttempt.cycle_item_id == item_id)
        .scalar()
    )
    attempt_no = int(last_no or 0) + 1
    now = now_beijing()
    finished = body.status != "running"
    attempt = TestCycleAttempt(
        project_id=project_id,
        cycle_id=cycle_id,
        cycle_item_id=item_id,
        asset_type=item.asset_type or "functional",
        asset_id=item.asset_id if item.asset_id is not None else item.case_id,
        execution_type=body.execution_type,
        original_run_type=body.original_run_type,
        original_run_id=body.original_run_id,
        attempt_no=attempt_no,
        status=body.status,
        summary=(body.summary or "")[:65535],
        evidence_json=body.evidence if isinstance(body.evidence, dict) else None,
        started_at=now,
        finished_at=now if finished else None,
        created_by_id=user.id,
    )
    db.add(attempt)
    item.attempt_no = attempt_no
    if body.original_run_type and body.original_run_id is not None:
        item.last_run_type = body.original_run_type
        item.last_run_id = body.original_run_id
    if finished:
        mapped = _ATTEMPT_TO_ITEM_RESULT.get(body.status, "failed")
        item.result = mapped
        if body.summary:
            item.actual_result = body.summary[:65535]
        item.executed_by_id = user.id
        item.executed_at = now
        if mapped in _RESULT_SYNC_STATUSES and item.case_id is not None:
            case = db.get(TestCase, item.case_id)
            if case is not None and case.project_id == project_id:
                record_execution_result(case, mapped, at=now)
        elif mapped == "untested" and item.case_id is not None:
            case = db.get(TestCase, item.case_id)
            if case is not None and case.project_id == project_id:
                record_execution_result(case, "untested")
    cycle.updated_at = now
    db.commit()
    db.refresh(attempt)
    return CycleAttemptOut.model_validate(attempt)


@router.get(
    "/cycles/{cycle_id}/items/{item_id}/attempts",
    response_model=list[CycleAttemptOut],
)
def list_item_attempts(
    project_id: int,
    cycle_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[CycleAttemptOut]:
    _require_item(db, project_id, cycle_id, item_id)
    rows = (
        db.query(TestCycleAttempt)
        .filter(TestCycleAttempt.cycle_item_id == item_id)
        .order_by(TestCycleAttempt.attempt_no.asc(), TestCycleAttempt.id.asc())
        .all()
    )
    return [CycleAttemptOut.model_validate(r) for r in rows]


@router.get("/cycles/{cycle_id}/attempts", response_model=list[CycleAttemptOut])
def list_cycle_attempts(
    project_id: int,
    cycle_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    limit: int = Query(200, ge=1, le=500),
) -> list[CycleAttemptOut]:
    _require_cycle(db, project_id, cycle_id)
    rows = (
        db.query(TestCycleAttempt)
        .filter(TestCycleAttempt.cycle_id == cycle_id)
        .order_by(TestCycleAttempt.id.desc())
        .limit(limit)
        .all()
    )
    return [CycleAttemptOut.model_validate(r) for r in rows]


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
