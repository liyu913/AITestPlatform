"""Unified execution list across manual items, API runs and UI runs.

Each source keeps its own detail page and evidence; this endpoint only merges
and filters them so "执行中心" can answer "what ran in this project, in what
batch, with what result" without three separate lists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.deps import get_db, require_project_role
from app.models.api_automation.api_automation import ApiCase, ApiRun, ApiSuite
from app.models.functional.test_plan import TestCycle, TestCycleItem, TestPlan
from app.models.functional.testcase import TestCase
from app.models.project.project import ProjectMember
from app.models.ui_automation.ui_automation import UiCase, UiRun, UiSuite
from app.schemas.project.execution import ExecutionPage, ExecutionRow

router = APIRouter(prefix="/projects/{project_id}", tags=["executions"])

KindParam = Literal["all", "manual", "api", "ui"]

# Status vocabularies differ between manual items and automation runs.
_STATUS_ALIASES = {
    "untested": ("untested",),
    "pending": ("pending", "running", "untested"),
    "running": ("running", "pending"),
    "blocked": ("blocked",),
    "passed": ("passed",),
    "failed": ("failed", "error"),
    "error": ("error", "failed"),
    "cancelled": ("cancelled",),
}


def _status_set(status: str | None) -> tuple[str, ...] | None:
    key = (status or "").strip().lower()
    if not key:
        return None
    return _STATUS_ALIASES.get(key, (key,))


def _asset_name_maps(db: Session, project_id: int) -> dict[tuple[str, int], str]:
    """(asset_type, id) → display name, loaded in one pass per asset kind."""
    out: dict[tuple[str, int], str] = {}
    for asset_type, model in (
        ("api_case", ApiCase),
        ("api_suite", ApiSuite),
        ("ui_case", UiCase),
        ("ui_suite", UiSuite),
    ):
        for row_id, name in db.query(model.id, model.name).filter(model.project_id == project_id).all():
            out[(asset_type, int(row_id))] = str(name or "")
    return out


def _batch_name(plan_name: str | None, cycle_name: str | None) -> str | None:
    if not plan_name and not cycle_name:
        return None
    if plan_name and cycle_name:
        return f"{plan_name} / {cycle_name}"
    return plan_name or cycle_name


def _manual_rows(
    db: Session,
    project_id: int,
    *,
    statuses: tuple[str, ...] | None,
    limit: int,
) -> tuple[list[ExecutionRow], int]:
    base = (
        db.query(TestCycleItem, TestCycle, TestPlan)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestPlan.project_id == project_id)
    )
    if statuses:
        base = base.filter(TestCycleItem.result.in_(statuses))
    total = int(base.count() or 0)
    rows = (
        base.order_by(TestCycleItem.updated_at.desc(), TestCycleItem.id.desc())
        .limit(limit)
        .all()
    )
    case_ids = [int(item.case_id) for item, _, _ in rows if item.case_id is not None]
    titles = (
        {int(i): str(t) for i, t in db.query(TestCase.id, TestCase.title).filter(TestCase.id.in_(case_ids)).all()}
        if case_ids
        else {}
    )
    assets = _asset_name_maps(db, project_id) if any(item.case_id is None for item, _, _ in rows) else {}
    out: list[ExecutionRow] = []
    for item, cycle, plan in rows:
        asset_id = item.asset_id if item.asset_id is not None else item.case_id
        title = (
            titles.get(int(item.case_id), "")
            if item.case_id is not None
            else assets.get((item.asset_type or "", int(asset_id or 0)), "")
        ) or f"执行项 #{item.id}"
        out.append(
            ExecutionRow(
                key=f"manual:{item.id}",
                kind="manual",
                id=int(item.id),
                title=title,
                status=item.result or "untested",
                batch=_batch_name(plan.name, cycle.name),
                cycle_id=int(cycle.id),
                cycle_item_id=int(item.id),
                attempt_no=int(item.attempt_no or 0),
                target_type=item.asset_type or "functional",
                target_id=int(asset_id) if asset_id is not None else None,
                link=f"cycles/{cycle.id}?item={item.id}",
                started_at=item.executed_at,
                finished_at=item.executed_at,
                updated_at=item.updated_at or now_beijing(),
            )
        )
    return out, total


def _run_batch_map(db: Session, project_id: int, item_ids: set[int]) -> dict[int, tuple[int, str | None]]:
    """cycle_item_id → (cycle_id, batch display name) for run rows."""
    if not item_ids:
        return {}
    rows = (
        db.query(TestCycleItem.id, TestCycle, TestPlan)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycleItem.id.in_(item_ids), TestPlan.project_id == project_id)
        .all()
    )
    return {int(item_id): (int(cycle.id), _batch_name(plan.name, cycle.name)) for item_id, cycle, plan in rows}


def _run_rows(
    db: Session,
    project_id: int,
    *,
    kind: Literal["api", "ui"],
    model: type,
    statuses: tuple[str, ...] | None,
    limit: int,
) -> tuple[list[ExecutionRow], int]:
    base = db.query(model).filter(model.project_id == project_id)
    if statuses:
        base = base.filter(model.status.in_(statuses))
    total = int(base.count() or 0)
    rows = base.order_by(model.id.desc()).limit(limit).all()
    assets = _asset_name_maps(db, project_id)
    batch_map = _run_batch_map(db, project_id, {int(r.cycle_item_id) for r in rows if r.cycle_item_id})
    out: list[ExecutionRow] = []
    for run in rows:
        target_type = None
        if getattr(run, "trigger_type", "case") == "suite":
            target_type = f"{kind}_suite"
        else:
            target_type = f"{kind}_case"
        title = assets.get((target_type, int(run.target_id))) or f"{kind} 运行 #{run.id}"
        cycle_id, batch = batch_map.get(int(run.cycle_item_id), (None, None)) if run.cycle_item_id else (None, None)
        out.append(
            ExecutionRow(
                key=f"{kind}:{run.id}",
                kind=kind,
                id=int(run.id),
                title=title,
                status=str(run.status),
                batch=batch,
                cycle_id=cycle_id,
                cycle_item_id=int(run.cycle_item_id) if run.cycle_item_id else None,
                target_type=target_type,
                target_id=int(run.target_id),
                link=f"{kind}-runs/{run.id}",
                started_at=_iso(run.started_at),
                finished_at=_iso(run.finished_at),
                updated_at=run.updated_at or now_beijing(),
            )
        )
    return out, total


def _iso(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


@router.get("/executions", response_model=ExecutionPage)
def list_executions(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    kind: KindParam = Query("all"),
    status: str | None = Query(None, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ExecutionPage:
    statuses = _status_set(status)
    # Fetch enough from every source so the merged slice is complete.
    fetch = page * page_size
    collected: list[ExecutionRow] = []
    counts: dict[str, int] = {}

    if kind in ("all", "manual"):
        rows, total = _manual_rows(db, project_id, statuses=statuses, limit=fetch)
        collected.extend(rows)
        counts["manual"] = total
    if kind in ("all", "api"):
        rows, total = _run_rows(db, project_id, kind="api", model=ApiRun, statuses=statuses, limit=fetch)
        collected.extend(rows)
        counts["api"] = total
    if kind in ("all", "ui"):
        rows, total = _run_rows(db, project_id, kind="ui", model=UiRun, statuses=statuses, limit=fetch)
        collected.extend(rows)
        counts["ui"] = total

    collected.sort(key=lambda row: (row.updated_at, row.kind, row.id), reverse=True)
    start = (page - 1) * page_size
    return ExecutionPage(
        items=collected[start : start + page_size],
        total=sum(counts.values()),
        page=page,
        page_size=page_size,
        counts=counts,
    )
