"""Project-wide search used by the top bar.

Only titles (and defect/requirement body snippets) are searched: it is a jump
helper, not a full-text index. Every hit carries the route that opens it in the
owning module.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_project_role
from app.models.api_automation.api_automation import ApiCase
from app.models.functional.defect import Defect
from app.models.functional.requirement import Requirement
from app.models.functional.test_plan import TestCycle, TestPlan
from app.models.functional.testcase import TestCase
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiCase
from app.schemas.project.search import SearchGroup, SearchHit, SearchOut

router = APIRouter(prefix="/projects/{project_id}/search", tags=["search"])

PER_GROUP = 5


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _requirement_group(db: Session, project_id: int, pattern: str) -> SearchGroup:
    rows = (
        db.query(Requirement)
        .filter(
            Requirement.project_id == project_id,
            or_(Requirement.title.like(pattern, escape="\\"), Requirement.body_md.like(pattern, escape="\\")),
        )
        .order_by(Requirement.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    return SearchGroup(
        kind="requirement",
        label="需求",
        items=[
            SearchHit(
                kind="requirement",
                id=row.id,
                title=row.title,
                subtitle=(row.body_md or "").strip().replace("\n", " ")[:80] or None,
                link=f"requirements?requirement={row.id}",
            )
            for row in rows
        ],
    )


def _case_group(db: Session, project_id: int, pattern: str) -> SearchGroup:
    rows = (
        db.query(TestCase)
        .filter(TestCase.project_id == project_id, TestCase.title.like(pattern, escape="\\"))
        .order_by(TestCase.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    return SearchGroup(
        kind="test_case",
        label="功能用例",
        items=[
            SearchHit(
                kind="test_case",
                id=row.id,
                title=row.title,
                subtitle=f"v{max(1, int(row.version_no or 1))} · {row.lifecycle_status or row.status}",
                link=f"test-cases?case={row.id}",
            )
            for row in rows
        ],
    )


def _plan_group(db: Session, project_id: int, pattern: str) -> SearchGroup:
    plans = (
        db.query(TestPlan)
        .filter(TestPlan.project_id == project_id, TestPlan.name.like(pattern, escape="\\"))
        .order_by(TestPlan.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    cycles = (
        db.query(TestCycle)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestPlan.project_id == project_id, TestCycle.name.like(pattern, escape="\\"))
        .order_by(TestCycle.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    items = [
        SearchHit(
            kind="test_plan",
            id=row.id,
            title=row.name,
            subtitle=row.version or row.status,
            link=f"test-plans/{row.id}",
        )
        for row in plans
    ] + [
        SearchHit(
            kind="test_cycle",
            id=row.id,
            title=row.name,
            subtitle=f"批次 · {row.status}",
            link=f"cycles/{row.id}",
        )
        for row in cycles
    ]
    return SearchGroup(kind="test_plan", label="计划与批次", items=items[:PER_GROUP])


def _defect_group(db: Session, project_id: int, pattern: str) -> SearchGroup:
    rows = (
        db.query(Defect)
        .filter(Defect.project_id == project_id, Defect.title.like(pattern, escape="\\"))
        .order_by(Defect.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    return SearchGroup(
        kind="defect",
        label="缺陷",
        items=[
            SearchHit(
                kind="defect",
                id=row.id,
                title=row.title,
                subtitle=f"{row.status} · {row.category}",
                link=f"defects?defect={row.id}",
            )
            for row in rows
        ],
    )


def _automation_groups(db: Session, project_id: int, pattern: str) -> list[SearchGroup]:
    api_cases = (
        db.query(ApiCase)
        .filter(ApiCase.project_id == project_id, ApiCase.name.like(pattern, escape="\\"))
        .order_by(ApiCase.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    ui_cases = (
        db.query(UiCase)
        .filter(UiCase.project_id == project_id, UiCase.name.like(pattern, escape="\\"))
        .order_by(UiCase.updated_at.desc())
        .limit(PER_GROUP)
        .all()
    )
    groups: list[SearchGroup] = []
    if api_cases:
        groups.append(
            SearchGroup(
                kind="api_case",
                label="接口用例",
                items=[
                    SearchHit(
                        kind="api_case",
                        id=row.id,
                        title=row.name,
                        subtitle=str(row.path or ""),
                        link=f"api-cases?caseId={row.id}",
                    )
                    for row in api_cases
                ],
            )
        )
    if ui_cases:
        groups.append(
            SearchGroup(
                kind="ui_case",
                label="UI 用例",
                items=[
                    SearchHit(
                        kind="ui_case",
                        id=row.id,
                        title=row.name,
                        subtitle=row.status,
                        link=f"ui-cases?case={row.id}",
                    )
                    for row in ui_cases
                ],
            )
        )
    return groups


@router.get("", response_model=SearchOut)
def project_search(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    q: str = Query("", max_length=128),
) -> SearchOut:
    term = (q or "").strip()
    if len(term) < 1:
        return SearchOut(query=term, groups=[], total=0)
    pattern = _like(term)
    groups = [
        _requirement_group(db, project_id, pattern),
        _case_group(db, project_id, pattern),
        _plan_group(db, project_id, pattern),
        _defect_group(db, project_id, pattern),
        *_automation_groups(db, project_id, pattern),
    ]
    groups = [group for group in groups if group.items]
    return SearchOut(
        query=term,
        groups=groups,
        total=sum(len(group.items) for group in groups),
    )
