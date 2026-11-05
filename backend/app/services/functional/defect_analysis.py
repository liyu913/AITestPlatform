"""Defect analysis lifecycle and submission attempts (R4)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing
from app.errors import AppError
from app.models.functional.defect import Defect
from app.models.functional.defect_analysis import (
    ANALYSIS_CATEGORIES,
    DefectAnalysis,
    DefectSubmission,
)
from app.models.functional.test_plan import TestCycle, TestCycleAttempt, TestCycleItem, TestPlan
from app.models.functional.testcase import TestCase
from app.schemas.functional.defect_analysis import AnalysisCreate, AnalysisUpdate


def _require_item(db: Session, project_id: int, item_id: int) -> TestCycleItem:
    item = (
        db.query(TestCycleItem)
        .join(TestCycle, TestCycleItem.cycle_id == TestCycle.id)
        .join(TestPlan, TestCycle.plan_id == TestPlan.id)
        .filter(TestCycleItem.id == item_id, TestPlan.project_id == project_id)
        .first()
    )
    if item is None:
        raise AppError("not_found", "执行项不存在", status_code=404)
    return item


def _attempt_evidence(db: Session, item_id: int) -> dict:
    attempts = (
        db.query(TestCycleAttempt)
        .filter(TestCycleAttempt.cycle_item_id == item_id)
        .order_by(TestCycleAttempt.attempt_no.desc())
        .all()
    )
    screenshots: list[str] = []
    for attempt in attempts:
        payload = attempt.evidence_json if isinstance(attempt.evidence_json, dict) else {}
        screenshots.extend(str(key) for key in payload.get("screenshots") or [])
    return {
        "attempt_ids": [a.id for a in attempts],
        "screenshots": screenshots,
        "last_summary": attempts[0].summary if attempts else "",
    }


def create_analysis(
    db: Session,
    *,
    project_id: int,
    payload: AnalysisCreate,
    user_id: int | None,
) -> DefectAnalysis:
    source_type = "manual"
    source_id: int | None = None
    title = (payload.title or "").strip()
    observed = (payload.observed or "").strip()
    expected = (payload.expected or "").strip()
    evidence: dict | None = None

    if payload.cycle_item_id is not None:
        item = _require_item(db, project_id, payload.cycle_item_id)
        case = db.get(TestCase, item.case_id) if item.case_id else None
        source_type = "cycle_item"
        source_id = item.id
        title = title or f"{case.title if case else '执行项'} 失败分析"
        observed = observed or (item.actual_result or "").strip()
        expected = expected or ((case.overall_expected if case else "") or "").strip()
        evidence = _attempt_evidence(db, item.id)
        evidence["cycle_item_id"] = item.id
        evidence["cycle_id"] = item.cycle_id
    elif payload.defect_id is not None:
        defect = (
            db.query(Defect)
            .filter(Defect.id == payload.defect_id, Defect.project_id == project_id)
            .first()
        )
        if defect is None:
            raise AppError("not_found", "缺陷不存在", status_code=404)
        source_type = "defect"
        source_id = defect.id
        title = title or defect.title
        observed = observed or (defect.actual_result or "").strip()
        expected = expected or (defect.expected_result or "").strip()
    elif not title:
        raise AppError("bad_request", "手工分析需要填写标题", status_code=400)

    if payload.category not in ANALYSIS_CATEGORIES:
        raise AppError("bad_request", "未知的分析分类", status_code=400)

    row = DefectAnalysis(
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        title=title[:512],
        observed=observed,
        expected=expected,
        category=payload.category,
        conclusion=(payload.conclusion or "").strip(),
        status="confirmed" if payload.conclusion and payload.category != "pending" else "draft",
        evidence_json=evidence,
        created_by_id=user_id,
    )
    if row.status == "confirmed":
        row.confirmed_by_id = user_id
        row.confirmed_at = now_beijing()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_analysis(
    db: Session,
    *,
    analysis: DefectAnalysis,
    payload: AnalysisUpdate,
    user_id: int | None,
) -> DefectAnalysis:
    for field in ("title", "observed", "expected", "category", "conclusion"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(analysis, field, value.strip() if isinstance(value, str) else value)
    if analysis.category not in ANALYSIS_CATEGORIES:
        raise AppError("bad_request", "未知的分析分类", status_code=400)

    if payload.status == "confirmed":
        if analysis.category == "pending":
            raise AppError("bad_request", "确认分析前需要选择分类", status_code=400)
        if not (analysis.conclusion or "").strip():
            raise AppError("bad_request", "确认分析需要填写结论", status_code=400)
        analysis.status = "confirmed"
        analysis.confirmed_by_id = user_id
        analysis.confirmed_at = now_beijing()
    elif payload.status == "dismissed":
        if analysis.category == "product":
            raise AppError(
                "bad_request", "产品问题不能直接结束分析，请转为正式缺陷", status_code=400
            )
        if not (analysis.conclusion or "").strip():
            raise AppError("bad_request", "结束分析需要填写原因", status_code=400)
        analysis.status = "dismissed"
        analysis.confirmed_by_id = user_id
        analysis.confirmed_at = now_beijing()
    db.commit()
    db.refresh(analysis)
    return analysis


def convert_to_defect(
    db: Session, *, analysis: DefectAnalysis, user_id: int | None
) -> Defect:
    if analysis.defect_id is not None:
        raise AppError("conflict", "该分析已转为缺陷", status_code=409)
    if analysis.status != "confirmed":
        raise AppError("bad_request", "只有已确认的分析可以转为缺陷", status_code=400)
    if analysis.category != "product":
        raise AppError("bad_request", "只有产品类问题需要提交正式缺陷", status_code=400)

    item = None
    if analysis.source_type == "cycle_item" and analysis.source_id is not None:
        item = db.get(TestCycleItem, analysis.source_id)
    case = db.get(TestCase, item.case_id) if item is not None and item.case_id else None
    cycle = db.get(TestCycle, item.cycle_id) if item is not None else None

    defect = Defect(
        project_id=analysis.project_id,
        title=analysis.title,
        description=analysis.conclusion or analysis.observed,
        severity="P2",
        status="open",
        category="product",
        case_id=case.id if case is not None else None,
        cycle_item_id=item.id if item is not None else None,
        requirement_id=case.requirement_id if case is not None else None,
        failure_source_type="analysis",
        failure_source_id=analysis.id,
        expected_result=analysis.expected,
        actual_result=analysis.observed,
        environment=(cycle.env_kind if cycle is not None else None),
        found_build=(cycle.target_build if cycle is not None else None),
        reporter_id=user_id,
    )
    db.add(defect)
    db.flush()
    analysis.defect_id = defect.id
    analysis.status = "converted"
    db.commit()
    db.refresh(defect)
    return defect


def list_analyses(
    db: Session, *, project_id: int, status: str | None = None, category: str | None = None
) -> list[DefectAnalysis]:
    query = db.query(DefectAnalysis).filter(DefectAnalysis.project_id == project_id)
    if status:
        query = query.filter(DefectAnalysis.status == status)
    if category:
        query = query.filter(DefectAnalysis.category == category)
    return query.order_by(DefectAnalysis.updated_at.desc(), DefectAnalysis.id.desc()).all()


def pending_submission(db: Session, *, project_id: int) -> list[Defect]:
    submitted = select(DefectSubmission.defect_id).where(
        DefectSubmission.project_id == project_id,
        DefectSubmission.status == "success",
    )
    return (
        db.query(Defect)
        .filter(Defect.project_id == project_id, Defect.id.notin_(submitted))
        .order_by(Defect.updated_at.desc(), Defect.id.desc())
        .all()
    )


def list_submissions(db: Session, *, project_id: int, defect_id: int) -> list[DefectSubmission]:
    return (
        db.query(DefectSubmission)
        .filter(
            DefectSubmission.project_id == project_id,
            DefectSubmission.defect_id == defect_id,
        )
        .order_by(DefectSubmission.id.desc())
        .all()
    )


def submit_defect(
    db: Session,
    *,
    defect: Defect,
    target: str,
    request_id: str | None,
    connector: str | None,
    user_id: int | None,
) -> DefectSubmission:
    rid = (request_id or uuid4().hex)[:64]
    existing = (
        db.query(DefectSubmission)
        .filter(
            DefectSubmission.project_id == defect.project_id,
            DefectSubmission.defect_id == defect.id,
            DefectSubmission.request_id == rid,
        )
        .first()
    )
    if existing is not None:
        return existing

    attempt_no = (
        db.query(DefectSubmission).filter(DefectSubmission.defect_id == defect.id).count() + 1
    )
    if target == "internal":
        row = DefectSubmission(
            project_id=defect.project_id,
            defect_id=defect.id,
            target="internal",
            connector=None,
            request_id=rid,
            status="success",
            external_id=f"LY-{defect.id}",
            message="已提交到平台内部缺陷库",
            attempt_no=attempt_no,
            submitted_by_id=user_id,
        )
        defect.external_system = "internal"
        defect.external_id = f"LY-{defect.id}"
    else:
        row = DefectSubmission(
            project_id=defect.project_id,
            defect_id=defect.id,
            target="external",
            connector=connector,
            request_id=rid,
            status="failed",
            external_id=None,
            message=(
                "外部连接器未配置，提交未完成；配置集成后用新的 request_id 重试"
                if not connector
                else f"外部连接器 {connector} 尚未实现，提交未完成"
            ),
            attempt_no=attempt_no,
            submitted_by_id=user_id,
        )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
