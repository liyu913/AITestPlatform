"""Defect triage: category suggestions, status history and close gating.

Closing a defect is the strongest claim this platform makes, so it is gated:
the original problem must have been retested successfully against the declared
fix build, and every linked regression batch must be finished without failures.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.errors import AppError
from app.models.functional.defect import Defect, DefectStatusHistory
from app.models.functional.test_plan import TestCycle, TestCycleItem, TestPlan
from app.models.functional.testcase import TestCase
from app.schemas.functional.defect import DefectSuggestionOut
from app.user_messages import (
    DEFECT_CLOSE_FAILED,
    DEFECT_CLOSE_NEEDS_FIX_BUILD,
    DEFECT_CLOSE_NEEDS_PASSED_RETEST,
    DEFECT_CLOSE_REGRESSION_INCOMPLETE,
    DEFECT_CLOSE_REGRESSION_NOT_FOUND,
)

_TERMINAL_BLOCKING_RESULTS = ("untested", "failed", "blocked")

# Ordered rules; first match wins. Each entry: (category, keywords, reason).
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "environment",
        ("环境", "超时", "连接", "网络", "dns", "证书", "502", "503", "504", "refused", "unreachable"),
        "错误信息更像环境或网络问题",
    ),
    (
        "data",
        ("数据", "重复", "不存在", "为空", "脏数据", "前置", "测试账号", "初始化"),
        "错误信息更像测试数据准备问题",
    ),
    (
        "script",
        ("定位", "选择器", "selector", "元素", "脚本", "等待", "未找到元素", "locator", "timeout waiting"),
        "错误信息更像执行脚本或定位问题",
    ),
    (
        "rule_pending",
        ("规则", "待确认", "需求未明确", "口径", "歧义"),
        "问题依赖尚未确认的业务规则",
    ),
    (
        "ux",
        ("体验", "文案", "提示不清晰", "布局", "样式", "颜色", "交互"),
        "问题更接近体验建议",
    ),
)

_CATEGORY_LABELS = {
    "product": "产品缺陷",
    "environment": "环境",
    "data": "数据",
    "script": "执行脚本",
    "rule_pending": "规则待确认",
    "ux": "体验建议",
}

_STATUS_LABELS = {
    "open": "待处理",
    "in_progress": "处理中",
    "resolved": "已修复",
    "reopened": "重新打开",
    "closed": "已关闭",
}


def category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category)


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def classify_failure(*texts: str | None) -> tuple[str, str]:
    haystack = " ".join(t for t in texts if t).lower()
    if not haystack.strip():
        return "product", "没有可用的错误信息，按产品缺陷处理"
    for category, keywords, reason in _CATEGORY_RULES:
        if any(keyword in haystack for keyword in keywords):
            return category, reason
    return "product", "错误信息符合产品行为不符合预期"


def suggestion_from_cycle_item(
    db: Session, project_id: int, item: TestCycleItem
) -> DefectSuggestionOut:
    case = db.get(TestCase, item.case_id) if item.case_id is not None else None
    cycle = db.get(TestCycle, item.cycle_id)
    plan = db.get(TestPlan, cycle.plan_id) if cycle is not None else None
    title = case.title if case is not None else f"执行项 #{item.id}"
    source_line = " / ".join(part for part in (plan.name if plan else "", cycle.name if cycle else "") if part)
    expected = ""
    if case is not None:
        overall = (case.overall_expected or "").strip()
        step_expectations = [
            (step.expected or "").strip()
            for step in sorted(case.steps or [], key=lambda s: s.order)
            if (step.expected or "").strip() and step.expected.strip() != "—"
        ]
        expected = overall or "\n".join(step_expectations)
    actual = (item.actual_result or "").strip()
    category, reason = classify_failure(actual, item.result)
    env_label = None
    snapshot = cycle.env_snapshot_json if cycle is not None and isinstance(cycle.env_snapshot_json, dict) else None
    if snapshot:
        env_label = str(snapshot.get("name") or snapshot.get("base_url") or "") or None
    build = (cycle.target_build if cycle is not None else None) or None

    description_parts = [f"来源：{source_line or '手工执行'}（执行项 #{item.id}）"]
    if expected:
        description_parts.append(f"预期结果：\n{expected}")
    if actual:
        description_parts.append(f"实际结果：\n{actual}")
    if env_label:
        description_parts.append(f"环境：{env_label}")

    return DefectSuggestionOut(
        category=category,
        severity=(case.priority if case is not None and case.priority else "P2"),
        title=f"[失败] {title}",
        description="\n".join(description_parts),
        expected_result=expected,
        actual_result=actual,
        environment=env_label,
        found_build=build,
        reasons=[reason, f"分类标签：{category_label(category)}"],
    )


def record_history(
    db: Session,
    defect: Defect,
    *,
    from_status: str,
    to_status: str,
    comment: str | None,
    actor_id: int | None,
) -> DefectStatusHistory:
    row = DefectStatusHistory(
        defect_id=defect.id,
        project_id=defect.project_id,
        from_status=from_status or "",
        to_status=to_status or "",
        comment=(comment or "").strip()[:65535],
        actor_id=actor_id,
    )
    db.add(row)
    return row


def _regression_cycles(db: Session, project_id: int, defect: Defect) -> list[TestCycle]:
    ids = defect.regression_cycle_ids_json if isinstance(defect.regression_cycle_ids_json, list) else []
    out: list[TestCycle] = []
    for raw in ids:
        try:
            cycle_id = int(raw)
        except (TypeError, ValueError):
            continue
        cycle = (
            db.query(TestCycle)
            .join(TestPlan, TestCycle.plan_id == TestPlan.id)
            .filter(TestCycle.id == cycle_id, TestPlan.project_id == project_id)
            .first()
        )
        if cycle is not None:
            out.append(cycle)
    return out


def close_blockers(db: Session, project_id: int, defect: Defect) -> list[str]:
    """Everything that still prevents closing this defect."""
    blockers: list[str] = []
    if not (defect.fix_build or "").strip():
        blockers.append(DEFECT_CLOSE_NEEDS_FIX_BUILD)
    if (defect.verification_result or "") != "passed" or defect.verified_at is None:
        blockers.append(DEFECT_CLOSE_NEEDS_PASSED_RETEST)

    for cycle in _regression_cycles(db, project_id, defect):
        items = db.query(TestCycleItem).filter(TestCycleItem.cycle_id == cycle.id).all()
        if not items:
            blockers.append(DEFECT_CLOSE_REGRESSION_INCOMPLETE.format(name=cycle.name, detail="批次没有执行项"))
            continue
        blocking = [i for i in items if (i.result or "untested") in _TERMINAL_BLOCKING_RESULTS]
        if blocking:
            detail = f"{len(blocking)}/{len(items)} 项未通过（含未测、失败、阻塞）"
            blockers.append(DEFECT_CLOSE_REGRESSION_INCOMPLETE.format(name=cycle.name, detail=detail))
    return blockers


def assert_can_close(db: Session, project_id: int, defect: Defect) -> None:
    blockers = close_blockers(db, project_id, defect)
    if blockers:
        raise AppError(
            "bad_request",
            DEFECT_CLOSE_FAILED + "：" + "；".join(blockers),
            status_code=400,
        )


def apply_verification(
    db: Session,
    defect: Defect,
    *,
    result: str,
    fix_build: str,
    note: str,
    actor_id: int | None,
) -> DefectStatusHistory:
    """Record a retest; a failure reopens the defect and clears the evidence."""
    now = now_beijing()
    before = defect.status
    defect.fix_build = fix_build.strip()
    defect.verification_note = (note or "").strip()[:65535]
    if result == "passed":
        defect.verification_result = "passed"
        defect.verified_at = now
        defect.verified_by_id = actor_id
        if before in ("open", "in_progress", "reopened"):
            defect.status = "resolved"
    else:
        defect.verification_result = "failed"
        defect.verified_at = now
        defect.verified_by_id = actor_id
        defect.status = "reopened"
        defect.closed_at = None
        defect.closed_by_id = None
    history = record_history(
        db,
        defect,
        from_status=before,
        to_status=defect.status,
        comment=f"复测{'通过' if result == 'passed' else '失败'}（构建 {defect.fix_build}）"
        + (f"：{note.strip()}" if (note or "").strip() else ""),
        actor_id=actor_id,
    )
    return history


def change_status(
    db: Session,
    project_id: int,
    defect: Defect,
    *,
    to_status: str,
    comment: str | None,
    actor_id: int | None,
) -> DefectStatusHistory | None:
    before = defect.status
    if to_status == before:
        return None
    if to_status == "closed":
        assert_can_close(db, project_id, defect)
        defect.closed_at = now_beijing()
        defect.closed_by_id = actor_id
    else:
        defect.closed_at = None
        defect.closed_by_id = None
        if to_status == "reopened" and defect.verification_result == "passed":
            # A manual reopen invalidates the previous passed retest.
            defect.verification_result = None
            defect.verified_at = None
            defect.verified_by_id = None
    defect.status = to_status
    return record_history(
        db,
        defect,
        from_status=before,
        to_status=to_status,
        comment=comment,
        actor_id=actor_id,
    )


def link_regression_cycles(
    db: Session, project_id: int, defect: Defect, cycle_ids: list[int]
) -> list[int]:
    unique: list[int] = []
    for raw in cycle_ids:
        cycle_id = int(raw)
        if cycle_id in unique:
            continue
        row = (
            db.query(TestCycle)
            .join(TestPlan, TestCycle.plan_id == TestPlan.id)
            .filter(TestCycle.id == cycle_id, TestPlan.project_id == project_id)
            .first()
        )
        if row is None:
            raise AppError("bad_request", DEFECT_CLOSE_REGRESSION_NOT_FOUND, status_code=400)
        unique.append(cycle_id)
    defect.regression_cycle_ids_json = unique or None
    return unique


def dedupe_existing(
    db: Session, project_id: int, failure_source_type: str | None, failure_source_id: int | None
) -> Defect | None:
    if not failure_source_type or failure_source_id is None:
        return None
    return (
        db.query(Defect)
        .filter(
            Defect.project_id == project_id,
            Defect.failure_source_type == failure_source_type,
            Defect.failure_source_id == failure_source_id,
        )
        .first()
    )


def as_summary(defect: Defect) -> dict[str, Any]:
    return {
        "id": defect.id,
        "status": defect.status,
        "status_label": status_label(defect.status),
        "category": defect.category,
        "category_label": category_label(defect.category),
        "fix_build": defect.fix_build,
        "verification_result": defect.verification_result,
    }
