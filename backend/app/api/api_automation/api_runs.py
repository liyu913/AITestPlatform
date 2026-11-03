from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.deps import SessionLocal, get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiRun, ApiRunStep
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.api_automation.api_automation import ApiRunOut, ApiRunStepOut
from app.schemas.api_automation.api_scenario_agent import ScenarioTriageConfirm, ScenarioTriageOut
from app.schemas.page import Page
from app.services.api_automation.api_report import build_html_report, build_junit_xml
from app.services.api_automation.api_scenario_events import get_event_bus
from app.services.api_automation.api_scenario_triage import (
    get_triage_graph,
    new_triage_thread_id,
    reset_triage_graph,
    resume_triage,
    run_triage_until_interrupt,
)
from app.services.ai.effective_ai_settings import get_llm_runtime
from app.user_messages import API_RUN_NOT_FOUND

router = APIRouter(prefix="/projects/{project_id}/api-runs", tags=["api-runs"])

# in-memory triage sessions: triage_id -> meta
_triage_store: dict[str, dict] = {}
_triage_db_factory_override = None


def set_triage_db_factory(factory) -> None:
    global _triage_db_factory_override
    _triage_db_factory_override = factory
    reset_triage_graph()
    get_triage_graph(db_factory=_triage_db_factory)


def _triage_db_factory() -> Session:
    if _triage_db_factory_override is not None:
        return _triage_db_factory_override()
    return SessionLocal()


get_triage_graph(db_factory=_triage_db_factory)


def _clamp_pagination(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, page_size))


def _require_run(db: Session, project_id: int, run_id: int) -> ApiRun:
    row = db.query(ApiRun).filter(ApiRun.id == run_id, ApiRun.project_id == project_id).first()
    if row is None:
        raise AppError("not_found", API_RUN_NOT_FOUND, status_code=404)
    return row


@router.get("/", response_model=Page[ApiRunOut])
def list_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, max_length=32),
) -> Page[ApiRunOut]:
    p, ps = _clamp_pagination(page, page_size)
    filtered = db.query(ApiRun).filter(ApiRun.project_id == project_id)
    if status and status.strip():
        filtered = filtered.filter(ApiRun.status == status.strip())
    total = filtered.count()
    rows = filtered.order_by(ApiRun.updated_at.desc(), ApiRun.id.desc()).offset((p - 1) * ps).limit(ps).all()
    return Page[ApiRunOut](
        items=[ApiRunOut.model_validate(r) for r in rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.get("/{run_id}", response_model=ApiRunOut)
def get_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ApiRunOut:
    return ApiRunOut.model_validate(_require_run(db, project_id, run_id))


@router.get("/{run_id}/steps", response_model=list[ApiRunStepOut])
def list_run_steps(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> list[ApiRunStepOut]:
    _require_run(db, project_id, run_id)
    rows = (
        db.query(ApiRunStep)
        .filter(ApiRunStep.run_id == run_id, ApiRunStep.project_id == project_id)
        .order_by(ApiRunStep.order, ApiRunStep.id)
        .all()
    )
    return [ApiRunStepOut.model_validate(r) for r in rows]


@router.get("/{run_id}/export")
def export_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    format: str = Query("junit", pattern="^(junit|html)$"),
) -> Response:
    run = _require_run(db, project_id, run_id)
    rows = (
        db.query(ApiRunStep)
        .filter(ApiRunStep.run_id == run_id, ApiRunStep.project_id == project_id)
        .order_by(ApiRunStep.order, ApiRunStep.id)
        .all()
    )
    steps = [
        {
            "order": r.order,
            "case_id": r.case_id,
            "case_name": r.case_name,
            "status": r.status,
            "response_status": r.response_status,
            "duration_ms": r.duration_ms,
            "error_message": r.error_message or "",
            "assertion_results_json": r.assertion_results_json if isinstance(r.assertion_results_json, list) else [],
            "response_body_text": r.response_body_text or "",
        }
        for r in rows
    ]
    if format == "html":
        content = build_html_report(
            run_id=run.id,
            project_id=project_id,
            status=run.status,
            summary=run.summary_json if isinstance(run.summary_json, dict) else None,
            steps=steps,
        )
        return Response(
            content=content,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="api-run-{run_id}.html"'},
        )
    content = build_junit_xml(run_id=run.id, project_id=project_id, status=run.status, steps=steps)
    return Response(
        content=content,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="api-run-{run_id}.xml"'},
    )


def _triage_out(meta: dict) -> ScenarioTriageOut:
    tid = meta["thread_id"]
    events = get_event_bus().snapshot(tid)
    return ScenarioTriageOut(
        id=meta["id"],
        run_id=meta["run_id"],
        step_id=meta["step_id"],
        status=meta["status"],
        plan=meta.get("plan"),
        events=events,
        error_message=meta.get("error_message") or "",
        patch_message=meta.get("patch_message") or "",
    )


@router.post("/{run_id}/steps/{step_id}/ai-triage", response_model=ScenarioTriageOut, status_code=201)
def start_step_triage(
    project_id: int,
    run_id: int,
    step_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    __: User = Depends(get_current_user),
) -> ScenarioTriageOut:
    _require_run(db, project_id, run_id)
    step = (
        db.query(ApiRunStep)
        .filter(
            ApiRunStep.id == step_id,
            ApiRunStep.run_id == run_id,
            ApiRunStep.project_id == project_id,
        )
        .first()
    )
    if step is None:
        raise AppError("not_found", "运行步骤不存在", status_code=404)
    if step.status not in ("failed", "error"):
        raise AppError("bad_request", "仅支持对失败/错误步骤做归因", status_code=400)

    llm_cfg = get_llm_runtime(db)
    if not llm_cfg.api_key.strip():
        raise AppError("bad_request", "未配置 LLM API Key", status_code=400)

    thread_id = new_triage_thread_id()
    get_event_bus().replace(thread_id, [])
    triage_id = thread_id
    result = run_triage_until_interrupt(
        db_factory=_triage_db_factory,
        thread_id=thread_id,
        project_id=project_id,
        run_id=run_id,
        step=step,
        llm_cfg=llm_cfg,
    )
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else None
    err = result.get("error")
    interrupted = bool(result.get("__interrupt__"))
    status = "error" if err else ("awaiting_confirm" if interrupted else "done")
    meta = {
        "id": triage_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "step_id": step_id,
        "project_id": project_id,
        "status": status,
        "plan": plan,
        "error_message": str(err) if err else "",
    }
    _triage_store[triage_id] = meta
    return _triage_out(meta)


@router.get("/{run_id}/ai-triage/{triage_id}", response_model=ScenarioTriageOut)
def get_step_triage(
    project_id: int,
    run_id: int,
    triage_id: str,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> ScenarioTriageOut:
    _require_run(db, project_id, run_id)
    meta = _triage_store.get(triage_id)
    if meta is None or meta.get("run_id") != run_id or meta.get("project_id") != project_id:
        raise AppError("not_found", "归因会话不存在", status_code=404)
    return _triage_out(meta)


@router.get("/{run_id}/ai-triage/{triage_id}/events")
def triage_events(
    project_id: int,
    run_id: int,
    triage_id: str,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> StreamingResponse:
    _require_run(db, project_id, run_id)
    meta = _triage_store.get(triage_id)
    if meta is None or meta.get("run_id") != run_id:
        raise AppError("not_found", "归因会话不存在", status_code=404)
    thread_id = meta["thread_id"]

    def gen():
        import json

        events = get_event_bus().snapshot(thread_id)
        for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/ai-triage/{triage_id}/confirm", response_model=ScenarioTriageOut)
def confirm_step_triage(
    project_id: int,
    run_id: int,
    triage_id: str,
    body: ScenarioTriageConfirm,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    __: User = Depends(get_current_user),
) -> ScenarioTriageOut:
    _require_run(db, project_id, run_id)
    meta = _triage_store.get(triage_id)
    if meta is None or meta.get("run_id") != run_id or meta.get("project_id") != project_id:
        raise AppError("not_found", "归因会话不存在", status_code=404)
    if meta.get("status") != "awaiting_confirm":
        raise AppError("bad_request", f"归因会话状态不可确认: {meta.get('status')}", status_code=400)

    result = resume_triage(
        db_factory=_triage_db_factory,
        thread_id=meta["thread_id"],
        action=body.action,
        apply_patch=body.apply_patch,
    )
    err = result.get("error")
    if body.action == "cancel":
        status = "cancelled"
    elif err:
        status = "error"
    else:
        status = "confirmed"
    meta["status"] = status
    meta["error_message"] = str(err) if err else ""
    meta["patch_message"] = str(result.get("patch_message") or "")
    if isinstance(result.get("plan"), dict):
        meta["plan"] = result["plan"]
    _triage_store[triage_id] = meta
    return _triage_out(meta)
