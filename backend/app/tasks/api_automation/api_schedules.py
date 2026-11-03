from __future__ import annotations

import logging

import app.deps as deps_mod
from app.models.api_automation.api_automation import ApiRun, ApiSchedule, ApiSuite
from app.services.api_automation.api_run_executor import execute_api_run
from app.services.api_automation.api_schedule_util import next_run_iso, parse_iso, to_iso, utc_now
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="tick_api_schedules")
def tick_api_schedules() -> int:
    """Poll due schedules and enqueue suite runs. Returns triggered count."""
    from app.tasks.api_automation.run_api_case import enqueue_run_api_case

    db = deps_mod.SessionLocal()
    triggered = 0
    try:
        now = utc_now()
        rows = db.query(ApiSchedule).filter(ApiSchedule.enabled.is_(True)).all()
        for sch in rows:
            nxt = parse_iso(sch.next_run_at)
            if nxt is not None and nxt > now:
                continue
            suite = (
                db.query(ApiSuite)
                .filter(ApiSuite.id == sch.suite_id, ApiSuite.project_id == sch.project_id)
                .first()
            )
            if suite is None:
                log.warning("schedule %s suite missing", sch.id)
                continue
            env_id = sch.env_id if sch.env_id is not None else suite.env_id
            run = ApiRun(
                project_id=sch.project_id,
                trigger_type="suite",
                target_id=suite.id,
                env_id=env_id,
                status="pending",
                triggered_by=sch.creator_id,
                summary_json={"trigger": "schedule", "schedule_id": sch.id},
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            try:
                task_id = enqueue_run_api_case(run.id)
                if task_id:
                    run.celery_task_id = task_id
                    db.commit()
            except Exception:
                log.exception("scheduled enqueue failed schedule=%s run=%s", sch.id, run.id)
                try:
                    execute_api_run(db, run.id)
                except Exception:
                    log.exception("scheduled sync failed schedule=%s run=%s", sch.id, run.id)
            sch.last_run_at = to_iso(now)
            try:
                sch.next_run_at = next_run_iso(sch.cron_expr, now)
            except Exception:
                sch.next_run_at = None
                sch.enabled = False
            db.commit()
            triggered += 1
        return triggered
    finally:
        db.close()
