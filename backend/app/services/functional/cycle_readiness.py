"""Execution readiness for a batch (R3).

Adding items to a draft batch is always allowed: planning can reference cases
that are still being reviewed. Starting the batch is the gate: every functional
item must point at a published case version, and the batch must have items.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.errors import AppError
from app.models.functional.test_plan import TestCycle, TestCycleItem
from app.models.functional.testcase import TestCase


def evaluate(db: Session, cycle: TestCycle) -> dict:
    items = (
        db.query(TestCycleItem)
        .filter(TestCycleItem.cycle_id == cycle.id)
        .order_by(TestCycleItem.id.asc())
        .all()
    )
    blockers: list[dict] = []
    warnings: list[str] = []

    if not items:
        blockers.append(
            {"item_id": None, "case_id": None, "title": None, "reason": "批次还没有计划项"}
        )

    for item in items:
        if item.asset_type == "functional":
            case_id = item.case_id or item.asset_id
            case = db.get(TestCase, case_id) if case_id else None
            if case is None:
                blockers.append(
                    {
                        "item_id": item.id,
                        "case_id": case_id,
                        "title": None,
                        "reason": "关联用例不存在",
                    }
                )
                continue
            if case.published_version_no is None:
                blockers.append(
                    {
                        "item_id": item.id,
                        "case_id": case.id,
                        "title": case.title,
                        "reason": "用例尚未发布正式版本",
                    }
                )
            elif item.asset_version and case.published_version_no != item.asset_version:
                warnings.append(
                    f"计划项 #{item.id} 记录 v{item.asset_version}，"
                    f"当前发布 v{case.published_version_no}"
                )

    if not cycle.env_kind:
        warnings.append("批次未指定执行环境")
    if not (cycle.target_build or "").strip():
        warnings.append("批次未指定目标构建")

    return {
        "cycle_id": cycle.id,
        "ready": not blockers,
        "checked_at": now_beijing().isoformat(),
        "items_total": len(items),
        "blockers": blockers,
        "warnings": warnings,
    }


def start_cycle(db: Session, cycle: TestCycle) -> dict:
    readiness = evaluate(db, cycle)
    if cycle.started_at is not None:
        return readiness
    if not readiness["ready"]:
        preview = "；".join(
            f"#{b['item_id']} {b['title'] or '计划项'}：{b['reason']}"
            for b in readiness["blockers"][:3]
        )
        raise AppError(
            "conflict",
            f"批次未就绪，无法开始执行：{preview}",
            status_code=409,
            detail=f"{len(readiness['blockers'])} 个阻塞项",
        )
    cycle.started_at = now_beijing()
    cycle.readiness_json = readiness
    cycle.status = "active"
    db.commit()
    db.refresh(cycle)
    return readiness
