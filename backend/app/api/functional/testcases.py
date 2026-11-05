from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.functional.testcase import TestCase, TestCaseReview, TestCaseStep, TestCaseVersion
from app.models.functional.requirement import Requirement
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiCase
from app.schemas.functional.design_trace import has_step_expectation
from app.schemas.functional.testcase import (
    TestCaseArchiveIn,
    TestCaseCreate,
    TestCaseOut,
    TestCaseReviewIn,
    TestCaseReviewOut,
    TestCaseStepOut,
    TestCaseUpdate,
    TestCaseVersionOut,
    clean_case_type,
    clean_precondition,
)
from app.schemas.page import Page
from app.services.functional.test_data_vars import as_str_dict, parse_test_data_text, variables_to_text
from app.services.functional.testcase_export import build_xlsx_bytes, build_xmind_bytes
from app.services.functional.testcase_lifecycle import (
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_CONFIRMED,
    LIFECYCLE_PENDING_REVIEW,
    archive_case,
    assert_deletable,
    ensure_baseline_version,
    normalize_lifecycle,
    restore_case,
    review_case,
    set_lifecycle_from_payload,
    snapshot_case_version,
    submit_for_review,
)
from app.services.functional.testcase_query import (
    EXPORT_CASE_LIMIT,
    apply_case_filters,
    distinct_modules,
)
from app.tasks.knowledge.reindex_entity import enqueue_reindex
from app.user_messages import (
    REQUIREMENT_NOT_IN_PROJECT,
    TEST_CASE_ARCHIVED_READONLY,
    TEST_CASE_EXPORT_TOO_MANY,
    TEST_CASE_NOT_FOUND,
    TEST_CASE_REVIEW_COMMENT_REQUIRED,
    TEST_CASE_VERSION_NOT_FOUND,
)

router = APIRouter(prefix="/projects/{project_id}/test-cases", tags=["test-cases"])

# Content fields whose change makes a new case version.
_VERSIONED_FIELDS = (
    "title",
    "module",
    "precondition",
    "test_data",
    "variables",
    "case_type",
    "overall_expected",
    "requirement_id",
    "priority",
    "tags",
    "steps",
)


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    p = max(1, page)
    ps = min(100, max(1, page_size))
    return p, ps


def _require_case(db: Session, project_id: int, case_id: int) -> TestCase:
    c = (
        db.query(TestCase)
        .filter(TestCase.id == case_id, TestCase.project_id == project_id)
        .options(selectinload(TestCase.steps))
        .first()
    )
    if c is None:
        raise AppError("not_found", TEST_CASE_NOT_FOUND, status_code=404)
    return c


def _validate_requirement_project(db: Session, project_id: int, requirement_id: int | None) -> None:
    if requirement_id is None:
        return
    r = db.get(Requirement, requirement_id)
    if r is None or r.project_id != project_id:
        raise AppError("bad_request", REQUIREMENT_NOT_IN_PROJECT, status_code=400)


def _clean_module(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _resolve_variables(
    *,
    variables: dict[str, str] | None,
    test_data: str | None,
    variables_provided: bool,
) -> dict[str, str]:
    """Prefer explicit variables; else parse free-form test_data."""
    if variables_provided:
        return as_str_dict(variables)
    parsed = parse_test_data_text(test_data)
    return parsed


def _linked_ui_counts(db: Session, project_id: int, case_ids: list[int]) -> dict[int, int]:
    if not case_ids:
        return {}
    rows = (
        db.query(UiCase.manual_case_id, func.count(UiCase.id))
        .filter(
            UiCase.project_id == project_id,
            UiCase.manual_case_id.in_(case_ids),
        )
        .group_by(UiCase.manual_case_id)
        .all()
    )
    return {int(cid): int(n) for cid, n in rows if cid is not None}


def _case_to_out(c: TestCase, *, linked_ui_case_count: int = 0) -> TestCaseOut:
    steps_sorted = sorted(c.steps, key=lambda s: s.order)
    tags = c.tags_json if isinstance(c.tags_json, list) else None
    variables = as_str_dict(c.variables_json)
    lifecycle = normalize_lifecycle(c.lifecycle_status)
    return TestCaseOut(
        ai_draft_id=c.ai_draft_id,
        design_trace=c.design_trace,
        id=c.id,
        project_id=c.project_id,
        requirement_id=c.requirement_id,
        title=c.title,
        module=_clean_module(c.module),
        precondition=clean_precondition(c.precondition),
        test_data=clean_precondition(c.test_data),
        variables=variables,
        case_type=clean_case_type(c.case_type),
        overall_expected=c.overall_expected or "",
        priority=c.priority,
        status=lifecycle,
        lifecycle_status=lifecycle,
        last_result=c.last_result,
        last_result_at=c.last_result_at,
        version_no=max(1, int(c.version_no or 1)),
        archived_at=c.archived_at,
        tags_json=tags,
        creator_id=c.creator_id,
        created_at=c.created_at,
        updated_at=c.updated_at,
        steps=[TestCaseStepOut.model_validate(s) for s in steps_sorted],
        linked_ui_case_count=max(0, int(linked_ui_case_count)),
    )


@router.get("/", response_model=Page[TestCaseOut])
def list_cases(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, max_length=256),
    requirement_id: int | None = Query(None, ge=1),
    module: str | None = Query(None, max_length=128),
    priority: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=64),
    lifecycle_status: str | None = Query(None, max_length=32),
    last_result: str | None = Query(None, max_length=32),
    include_archived: bool = Query(False),
) -> Page[TestCaseOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = apply_case_filters(
        db.query(TestCase),
        project_id=project_id,
        search=search,
        requirement_id=requirement_id,
        module=module,
        priority=priority,
        status=status,
        lifecycle_status=lifecycle_status,
        last_result=last_result,
        include_archived=include_archived,
    )
    total = filtered.count()
    rows = (
        filtered.options(selectinload(TestCase.steps))
        .order_by(TestCase.updated_at.desc(), TestCase.id.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )
    counts = _linked_ui_counts(db, project_id, [c.id for c in rows])
    return Page[TestCaseOut](
        items=[_case_to_out(c, linked_ui_case_count=counts.get(c.id, 0)) for c in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/modules", response_model=list[str])
def list_case_modules(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[str]:
    return distinct_modules(db, project_id)


@router.get("/export")
def export_cases(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    format: str = Query("xlsx", pattern="^(xlsx|xmind)$"),
    search: str | None = Query(None, max_length=256),
    requirement_id: int | None = Query(None, ge=1),
    module: str | None = Query(None, max_length=128),
    priority: str | None = Query(None, max_length=64),
    status: str | None = Query(None, max_length=64),
    lifecycle_status: str | None = Query(None, max_length=32),
) -> Response:
    filtered = apply_case_filters(
        db.query(TestCase),
        project_id=project_id,
        search=search,
        requirement_id=requirement_id,
        module=module,
        priority=priority,
        status=status,
        lifecycle_status=lifecycle_status,
    )
    total = filtered.count()
    if total > EXPORT_CASE_LIMIT:
        raise AppError("bad_request", TEST_CASE_EXPORT_TOO_MANY, status_code=400)
    rows = (
        filtered.options(selectinload(TestCase.steps))
        .order_by(TestCase.updated_at.desc(), TestCase.id.desc())
        .all()
    )
    if format == "xmind":
        content = build_xmind_bytes(rows)
        return Response(
            content=content,
            media_type="application/vnd.xmind.workbook",
            headers={
                "Content-Disposition": f'attachment; filename="test-cases-{project_id}.xmind"'
            },
        )
    content = build_xlsx_bytes(rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="test-cases-{project_id}.xlsx"'
        },
    )


@router.post("/", response_model=TestCaseOut, status_code=201)
def create_case(
    project_id: int,
    body: TestCaseCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCaseOut:
    _validate_requirement_project(db, project_id, body.requirement_id)
    # Create always accepts variables (default {}); empty + test_data → parse
    variables = _resolve_variables(
        variables=body.variables,
        test_data=body.test_data,
        variables_provided=bool(body.variables),
    )
    if not variables and body.test_data:
        variables = parse_test_data_text(body.test_data)
    test_data = body.test_data
    if variables and not test_data:
        test_data = variables_to_text(variables) or None
    c = TestCase(
        project_id=project_id,
        requirement_id=body.requirement_id,
        title=body.title,
        module=_clean_module(body.module),
        precondition=body.precondition,
        test_data=test_data,
        variables_json=variables or None,
        case_type=body.case_type,
        overall_expected=body.overall_expected,
        priority=body.priority,
        status="draft",
        lifecycle_status="draft",
        tags_json=body.tags,
        creator_id=user.id,
    )
    # Legacy payloads may still carry a status; execution words become
    # "confirmed + last_result" instead of polluting the lifecycle.
    set_lifecycle_from_payload(
        c,
        lifecycle_status=body.lifecycle_status,
        legacy_status=body.status,
        actor_id=user.id,
        import_mode=True,
    )
    db.add(c)
    db.flush()
    for st in body.steps:
        db.add(
            TestCaseStep(
                case_id=c.id,
                order=st.order,
                action=st.action,
                expected=st.expected,
            )
        )
    db.flush()
    c = (
        db.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == c.id)
        .one()
    )
    ensure_baseline_version(db, c, source="create", actor_id=user.id)
    db.commit()
    c = (
        db.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == c.id)
        .one()
    )
    enqueue_reindex(project_id, "case", c.id)
    return _case_to_out(c, linked_ui_case_count=_linked_ui_counts(db, project_id, [c.id]).get(c.id, 0))


@router.get("/{case_id}", response_model=TestCaseOut)
def get_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> TestCaseOut:
    c = _require_case(db, project_id, case_id)
    return _case_to_out(c, linked_ui_case_count=_linked_ui_counts(db, project_id, [c.id]).get(c.id, 0))


@router.patch("/{case_id}", response_model=TestCaseOut)
def update_case(
    project_id: int,
    case_id: int,
    body: TestCaseUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCaseOut:
    c = _require_case(db, project_id, case_id)
    if normalize_lifecycle(c.lifecycle_status) == LIFECYCLE_ARCHIVED:
        raise AppError("bad_request", TEST_CASE_ARCHIVED_READONLY, status_code=409)
    data = body.model_dump(exclude_unset=True)
    lifecycle_payload = data.pop("lifecycle_status", None)
    legacy_status = data.pop("status", None)
    change_note = data.pop("change_note", None)
    steps_payload = data.pop("steps", None)
    if c.design_trace and steps_payload is not None and (
        not steps_payload or any(not s["action"].strip() or not has_step_expectation(s["expected"]) for s in steps_payload)
    ):
        raise AppError("bad_request", "请为每步填写操作和可观察的预期结果", status_code=422)
    variables_payload = data.pop("variables", None)
    if "requirement_id" in data:
        _validate_requirement_project(db, project_id, data["requirement_id"])
    if "tags" in data:
        c.tags_json = data.pop("tags")
    if "module" in data:
        data["module"] = _clean_module(data["module"])
    if variables_payload is not None:
        vars_ = as_str_dict(variables_payload)
        c.variables_json = vars_ or None
        if "test_data" not in data:
            c.test_data = variables_to_text(vars_) or None
    elif "test_data" in data and data["test_data"] is not None:
        # Only sync variables from prose when variables were not explicitly sent
        parsed = parse_test_data_text(data["test_data"])
        if parsed:
            c.variables_json = parsed

    content_changed = bool(data) or variables_payload is not None or steps_payload is not None
    for key, val in data.items():
        setattr(c, key, val)
    if steps_payload is not None:
        # Delete through the ORM (not a bulk DELETE) so the identity map and the
        # loaded collection stay consistent when the new steps are inserted.
        for old_step in list(c.steps):
            db.delete(old_step)
        db.flush()
        db.expire(c, ["steps"])
        for i, st in enumerate(steps_payload):
            db.add(
                TestCaseStep(
                    case_id=c.id,
                    order=st.get("order", i),
                    action=st["action"],
                    expected=st["expected"],
                )
            )
        content_changed = True
    db.flush()
    c = (
        db.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == c.id)
        .populate_existing()
        .one()
    )
    if content_changed:
        # Editing confirmed content sends it back to review, unless the caller
        # changes the lifecycle explicitly in the same request.
        if (
            lifecycle_payload is None
            and legacy_status is None
            and normalize_lifecycle(c.lifecycle_status) == LIFECYCLE_CONFIRMED
        ):
            set_lifecycle_from_payload(c, lifecycle_status=LIFECYCLE_PENDING_REVIEW, actor_id=user.id)
        snapshot_case_version(
            db, c, source="edit", note=change_note, actor_id=user.id
        )
    set_lifecycle_from_payload(
        c,
        lifecycle_status=lifecycle_payload,
        legacy_status=legacy_status,
        actor_id=user.id,
    )
    db.commit()
    c = (
        db.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == c.id)
        .one()
    )
    enqueue_reindex(project_id, "case", c.id)
    return _case_to_out(c, linked_ui_case_count=_linked_ui_counts(db, project_id, [c.id]).get(c.id, 0))


@router.delete("/{case_id}", status_code=204)
def delete_case(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    c = _require_case(db, project_id, case_id)
    # Runs/defects/cycle items keep evidence; archive instead of destroying it.
    assert_deletable(db, c)
    db.delete(c)
    db.commit()


@router.get("/{case_id}/versions", response_model=list[TestCaseVersionOut])
def list_case_versions(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[TestCaseVersionOut]:
    c = _require_case(db, project_id, case_id)
    ensure_baseline_version(db, c, source="migration")
    db.commit()
    rows = (
        db.query(TestCaseVersion)
        .filter(TestCaseVersion.case_id == c.id)
        .order_by(TestCaseVersion.version_no.desc(), TestCaseVersion.id.desc())
        .all()
    )
    return [TestCaseVersionOut.model_validate(r) for r in rows]


@router.get("/{case_id}/versions/{version_no}", response_model=TestCaseVersionOut)
def get_case_version(
    project_id: int,
    case_id: int,
    version_no: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> TestCaseVersionOut:
    c = _require_case(db, project_id, case_id)
    row = (
        db.query(TestCaseVersion)
        .filter(TestCaseVersion.case_id == c.id, TestCaseVersion.version_no == version_no)
        .first()
    )
    if row is None:
        raise AppError("not_found", TEST_CASE_VERSION_NOT_FOUND, status_code=404)
    return TestCaseVersionOut.model_validate(row)


@router.post("/{case_id}/submit-review", response_model=TestCaseOut)
def submit_case_review(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCaseOut:
    c = _require_case(db, project_id, case_id)
    submit_for_review(c)
    db.commit()
    c = _require_case(db, project_id, case_id)
    return _case_to_out(c)


@router.post("/{case_id}/review", response_model=TestCaseOut)
def review_case_endpoint(
    project_id: int,
    case_id: int,
    body: TestCaseReviewIn,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCaseOut:
    c = _require_case(db, project_id, case_id)
    if body.decision == "rejected" and not (body.comment or "").strip():
        raise AppError("bad_request", TEST_CASE_REVIEW_COMMENT_REQUIRED, status_code=400)
    review_case(
        db,
        c,
        decision=body.decision,
        comment=body.comment,
        reviewer_id=user.id,
        version_id=body.version_id,
    )
    db.commit()
    c = _require_case(db, project_id, case_id)
    return _case_to_out(c)


@router.get("/{case_id}/reviews", response_model=list[TestCaseReviewOut])
def list_case_reviews(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[TestCaseReviewOut]:
    c = _require_case(db, project_id, case_id)
    rows = (
        db.query(TestCaseReview)
        .filter(TestCaseReview.case_id == c.id)
        .order_by(TestCaseReview.id.desc())
        .all()
    )
    return [TestCaseReviewOut.model_validate(r) for r in rows]


@router.post("/{case_id}/archive", response_model=TestCaseOut)
def archive_case_endpoint(
    project_id: int,
    case_id: int,
    body: TestCaseArchiveIn | None = None,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCaseOut:
    c = _require_case(db, project_id, case_id)
    archive_case(c, actor_id=user.id)
    if body is not None and (body.reason or "").strip():
        snapshot_case_version(
            db, c, source="archive", note=(body.reason or "").strip(), actor_id=user.id
        )
    db.commit()
    c = _require_case(db, project_id, case_id)
    return _case_to_out(c)


@router.post("/{case_id}/restore", response_model=TestCaseOut)
def restore_case_endpoint(
    project_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> TestCaseOut:
    c = _require_case(db, project_id, case_id)
    restore_case(c, actor_id=user.id)
    db.commit()
    c = _require_case(db, project_id, case_id)
    return _case_to_out(c)
