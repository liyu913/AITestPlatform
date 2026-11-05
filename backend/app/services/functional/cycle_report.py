"""Build and export immutable batch report snapshots (B6).

Statistics rules (fixed by the plan):
* pass rate = passed / (passed + failed); "not applicable" when that denominator is 0.
* completion = (passed + failed) / selected total; running/blocked/skipped/untested
  are reported separately instead of being folded into either number.
* retries never hide the first failure: a project that only passed after a retry
  is listed with its first failed attempt.
"""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from sqlalchemy.orm import Session, selectinload

from app.core.datetime_json import now_beijing
from app.models.functional.defect import Defect
from app.models.functional.requirement import Requirement
from app.models.functional.test_plan import (
    TestCycle,
    TestCycleItem,
    TestCycleReport,
    TestPlan,
)
from app.models.functional.testcase import TestCase
from app.services.functional.coverage_stats import requirement_coverage_rows

_ATTEMPT_FAILED = {"failed", "error", "blocked"}
_UNTESTED = "untested"


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _item_title(db: Session, item: TestCycleItem) -> str:
    if item.case_id is not None:
        case = db.get(TestCase, item.case_id)
        if case is not None:
            return case.title
    return f"执行项 #{item.id}"


def _attempt_insights(
    db: Session, items: list[TestCycleItem]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    retried_to_pass: list[dict[str, Any]] = []
    flaky: list[dict[str, Any]] = []
    running = 0
    for item in items:
        attempts = sorted(item.attempts or [], key=lambda a: a.attempt_no)
        statuses = [str(a.status) for a in attempts]
        if attempts and statuses[-1] == "running":
            running += 1
        failures = [a for a in attempts if str(a.status) in _ATTEMPT_FAILED]
        if not attempts:
            continue
        if item.result == "passed" and failures:
            first = failures[0]
            retried_to_pass.append(
                {
                    "item_id": item.id,
                    "title": _item_title(db, item),
                    "first_failed_attempt": first.attempt_no,
                    "first_failure": (first.summary or "")[:500],
                    "attempts": len(attempts),
                }
            )
        passed_attempts = [a for a in attempts if str(a.status) == "passed"]
        if passed_attempts and failures:
            flaky.append(
                {
                    "item_id": item.id,
                    "title": _item_title(db, item),
                    "statuses": statuses,
                }
            )
    return retried_to_pass, flaky, running


def build_report_snapshot(db: Session, project_id: int, cycle: TestCycle) -> dict[str, Any]:
    plan = db.get(TestPlan, cycle.plan_id)
    items = (
        db.query(TestCycleItem)
        .filter(TestCycleItem.cycle_id == cycle.id)
        .options(selectinload(TestCycleItem.attempts))
        .order_by(TestCycleItem.id)
        .all()
    )

    by_asset_type: dict[str, int] = {}
    by_result: dict[str, int] = {_UNTESTED: 0, "passed": 0, "failed": 0, "blocked": 0}
    for item in items:
        by_asset_type[item.asset_type or "functional"] = by_asset_type.get(item.asset_type or "functional", 0) + 1
        result = item.result or _UNTESTED
        by_result[result] = by_result.get(result, 0) + 1

    retried_to_pass, flaky, running = _attempt_insights(db, items)
    cancelled = sum(
        1
        for item in items
        if (item.attempts or []) and str(sorted(item.attempts, key=lambda a: a.attempt_no)[-1].status) == "cancelled"
    )
    skipped = cancelled

    total = len(items)
    passed = by_result.get("passed", 0)
    failed = by_result.get("failed", 0)
    blocked = by_result.get("blocked", 0)
    # Running/skipped are "not finished", so they must not also be counted as
    # untested; every plan item lands in exactly one bucket.
    untested = max(0, total - passed - failed - blocked - running - skipped)

    # Pinned case versions vs current content (source drift is reported, not hidden).
    source_versions: list[dict[str, Any]] = []
    drift = 0
    for item in items:
        if item.asset_type not in (None, "functional") or item.case_id is None:
            continue
        case = db.get(TestCase, item.case_id)
        if case is None:
            continue
        pinned = item.asset_version
        current = max(1, int(case.version_no or 1))
        drifted = pinned is not None and pinned != current
        if drifted:
            drift += 1
        source_versions.append(
            {
                "item_id": item.id,
                "case_id": case.id,
                "title": case.title,
                "pinned_version": pinned,
                "current_version": current,
                "drifted": drifted,
            }
        )

    coverage_rows = requirement_coverage_rows(db, project_id, cycle.id)
    acceptance_rules = {
        int(rid): (rules or "")
        for rid, rules in db.query(Requirement.id, Requirement.acceptance_rules)
        .filter(Requirement.project_id == project_id)
        .all()
    }
    requirements: list[dict[str, Any]] = []
    for row in coverage_rows:
        rules = acceptance_rules.get(row.requirement_id, "")
        if row.case_total > 0:
            kind = "case"
        elif rules.strip():
            kind = "rule"
        else:
            kind = "none"
        requirements.append(
            {
                "requirement_id": row.requirement_id,
                "title": row.title,
                "coverage_kind": kind,
                "case_total": row.case_total,
                "passed": row.passed,
                "failed": row.failed,
                "blocked": row.blocked,
                "untested": row.untested,
                "coverage_ratio": row.coverage_ratio,
                "blind": row.blind,
            }
        )

    item_ids = {item.id for item in items}
    linked_defects = (
        db.query(Defect).filter(Defect.project_id == project_id, Defect.cycle_item_id.in_(item_ids)).all()
        if item_ids
        else []
    )
    defect_by_status: dict[str, int] = {}
    for defect in linked_defects:
        defect_by_status[defect.status] = defect_by_status.get(defect.status, 0) + 1
    unresolved_defects = [
        {
            "id": d.id,
            "title": d.title,
            "status": d.status,
            "category": d.category,
            "severity": d.severity,
            "cycle_item_id": d.cycle_item_id,
        }
        for d in linked_defects
        if d.status != "closed"
    ]
    resolved_without_build = [
        d.id for d in linked_defects if d.status == "resolved" and not (d.fix_build or "").strip()
    ]

    risks: list[dict[str, str]] = []
    if total == 0:
        risks.append({"code": "empty_scope", "message": "本批次没有计划项", "severity": "high"})
    if untested:
        risks.append({"code": "untested", "message": f"{untested} 项未执行", "severity": "high"})
    if running:
        risks.append({"code": "running", "message": f"{running} 项仍在运行", "severity": "medium"})
    if blocked:
        risks.append({"code": "blocked", "message": f"{blocked} 项阻塞", "severity": "high"})
    if failed:
        risks.append({"code": "failed", "message": f"{failed} 项失败", "severity": "high"})
    if skipped:
        risks.append({"code": "skipped", "message": f"{skipped} 项已取消/跳过", "severity": "medium"})
    if retried_to_pass:
        risks.append(
            {
                "code": "flaky_retry",
                "message": f"{len(retried_to_pass)} 项重试后才通过（不稳定）",
                "severity": "medium",
            }
        )
    blind = [r for r in requirements if r["blind"]]
    if blind:
        risks.append(
            {"code": "requirement_blind", "message": f"{len(blind)} 个需求存在覆盖盲区", "severity": "medium"}
        )
    if unresolved_defects:
        risks.append(
            {
                "code": "open_defects",
                "message": f"{len(unresolved_defects)} 个关联缺陷未关闭",
                "severity": "high",
            }
        )
    if resolved_without_build:
        risks.append(
            {
                "code": "missing_fix_build",
                "message": f"{len(resolved_without_build)} 个已修复缺陷未登记修复构建",
                "severity": "low",
            }
        )
    if drift:
        risks.append(
            {
                "code": "case_version_drift",
                "message": f"{drift} 个计划项对应用例已更新（批次固定的是旧版本）",
                "severity": "medium",
            }
        )

    metrics = {
        "selected_total": total,
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "untested": untested,
        "running": running,
        "skipped": skipped,
        "pass_rate": _ratio(passed, passed + failed),
        "completion_rate": _ratio(passed + failed, total),
        "retried_to_pass": len(retried_to_pass),
        "flaky": len(flaky),
    }

    return {
        "generated_at": now_beijing().isoformat(),
        "cycle": {
            "id": cycle.id,
            "name": cycle.name,
            "status": cycle.status,
            "plan_id": cycle.plan_id,
            "plan_name": plan.name if plan else None,
            "target_build": cycle.target_build,
            "env_snapshot": cycle.env_snapshot_json if isinstance(cycle.env_snapshot_json, dict) else None,
            "credential_ref": cycle.credential_ref,
            "data_note": cycle.data_note or "",
        },
        "scope": {
            "total": total,
            "by_asset_type": by_asset_type,
            "by_result": by_result,
        },
        "metrics": metrics,
        "attempts": {
            "retried_to_pass": retried_to_pass,
            "flaky": flaky,
            "running": running,
        },
        "source_versions": source_versions,
        "requirements": requirements,
        "defects": {
            "linked": len(linked_defects),
            "by_status": defect_by_status,
            "unresolved": unresolved_defects,
        },
        "risks": risks,
    }


def summary_line(snapshot: dict[str, Any]) -> str:
    metrics = snapshot.get("metrics") or {}
    pass_rate = metrics.get("pass_rate")
    completion = metrics.get("completion_rate")
    pass_text = "不适用" if pass_rate is None else f"{round(pass_rate * 100)}%"
    completion_text = "不适用" if completion is None else f"{round(completion * 100)}%"
    return (
        f"通过率 {pass_text} · 完成比例 {completion_text} · "
        f"通过 {metrics.get('passed', 0)} / 失败 {metrics.get('failed', 0)} / "
        f"阻塞 {metrics.get('blocked', 0)} / 未执行 {metrics.get('untested', 0)}"
    )


def generate_report(db: Session, project_id: int, cycle: TestCycle, actor_id: int | None) -> TestCycleReport:
    revision = (
        db.query(TestCycleReport.revision)
        .filter(TestCycleReport.cycle_id == cycle.id)
        .order_by(TestCycleReport.revision.desc())
        .first()
    )
    next_revision = int(revision[0]) + 1 if revision else 1
    snapshot = build_report_snapshot(db, project_id, cycle)
    report = TestCycleReport(
        project_id=project_id,
        cycle_id=cycle.id,
        revision=next_revision,
        generated_at=now_beijing(),
        generated_by_id=actor_id,
        summary_text=summary_line(snapshot),
        snapshot_json=snapshot,
    )
    db.add(report)
    db.flush()
    return report


def report_to_markdown(snapshot: dict[str, Any]) -> str:
    cycle = snapshot.get("cycle") or {}
    metrics = snapshot.get("metrics") or {}
    lines = [
        f"# 测试报告：{cycle.get('name', '')}",
        "",
        f"- 计划：{cycle.get('plan_name') or '—'}",
        f"- 目标构建：{cycle.get('target_build') or '未知'}",
        f"- 生成时间：{snapshot.get('generated_at', '')}",
        "",
        "## 结果概览",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 选定计划项 | {metrics.get('selected_total', 0)} |",
        f"| 通过 | {metrics.get('passed', 0)} |",
        f"| 失败 | {metrics.get('failed', 0)} |",
        f"| 阻塞 | {metrics.get('blocked', 0)} |",
        f"| 未执行 | {metrics.get('untested', 0)} |",
        f"| 运行中 | {metrics.get('running', 0)} |",
        f"| 跳过/取消 | {metrics.get('skipped', 0)} |",
        f"| 通过率 | {_pct(metrics.get('pass_rate'))} |",
        f"| 完成比例 | {_pct(metrics.get('completion_rate'))} |",
        "",
        "## 需求覆盖",
        "",
        "| 需求 | 覆盖方式 | 用例数 | 通过 | 失败 | 阻塞 | 未测 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in snapshot.get("requirements") or []:
        lines.append(
            f"| {row['title']} | {_coverage_label(row['coverage_kind'])} | {row['case_total']} | "
            f"{row['passed']} | {row['failed']} | {row['blocked']} | {row['untested']} |"
        )
    lines.extend(["", "## 重试与不稳定", ""])
    retried = (snapshot.get("attempts") or {}).get("retried_to_pass") or []
    if retried:
        for row in retried:
            lines.append(
                f"- {row['title']}：第 {row['first_failed_attempt']} 次失败，最终通过（共 {row['attempts']} 次尝试）"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 遗留风险", ""])
    risks = snapshot.get("risks") or []
    if risks:
        for risk in risks:
            lines.append(f"- [{risk['severity']}] {risk['message']}")
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "不适用" if value is None else f"{round(value * 100, 2)}%"


def _coverage_label(kind: str) -> str:
    return {"case": "用例关联覆盖", "rule": "规则覆盖", "none": "无覆盖"}.get(kind, kind)


def report_to_xlsx(snapshot: dict[str, Any]) -> bytes:
    wb = Workbook()
    overview = wb.active
    assert overview is not None
    overview.title = "概览"
    cycle = snapshot.get("cycle") or {}
    metrics = snapshot.get("metrics") or {}
    overview.append(["项目", "值"])
    for label, value in (
        ("批次", cycle.get("name")),
        ("计划", cycle.get("plan_name")),
        ("目标构建", cycle.get("target_build") or "未知"),
        ("环境", (cycle.get("env_snapshot") or {}).get("name")),
        ("生成时间", snapshot.get("generated_at")),
        ("选定计划项", metrics.get("selected_total")),
        ("通过", metrics.get("passed")),
        ("失败", metrics.get("failed")),
        ("阻塞", metrics.get("blocked")),
        ("未执行", metrics.get("untested")),
        ("运行中", metrics.get("running")),
        ("跳过/取消", metrics.get("skipped")),
        ("通过率", _pct(metrics.get("pass_rate"))),
        ("完成比例", _pct(metrics.get("completion_rate"))),
    ):
        overview.append([label, value])

    coverage = wb.create_sheet("需求覆盖")
    coverage.append(["需求ID", "需求", "覆盖方式", "用例数", "通过", "失败", "阻塞", "未测", "盲区"])
    for row in snapshot.get("requirements") or []:
        coverage.append(
            [
                row["requirement_id"],
                row["title"],
                _coverage_label(row["coverage_kind"]),
                row["case_total"],
                row["passed"],
                row["failed"],
                row["blocked"],
                row["untested"],
                "是" if row["blind"] else "否",
            ]
        )

    retries = wb.create_sheet("重试与不稳定")
    retries.append(["执行项", "标题", "首次失败尝试", "首次失败摘要", "尝试次数"])
    for row in (snapshot.get("attempts") or {}).get("retried_to_pass") or []:
        retries.append(
            [row["item_id"], row["title"], row["first_failed_attempt"], row["first_failure"], row["attempts"]]
        )

    risks = wb.create_sheet("遗留风险")
    risks.append(["等级", "说明"])
    for risk in snapshot.get("risks") or []:
        risks.append([risk["severity"], risk["message"]])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
