from __future__ import annotations

import logging

import app.deps as deps_mod
from app.models.ui_automation.ui_automation import UiRun, UiSchedule, UiSuite
from app.services.api_automation.api_schedule_util import next_run_iso, parse_iso, to_iso, utc_now
from app.services.ui_automation.ui_run_executor import execute_ui_run
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="tick_ui_schedules")
def tick_ui_schedules() -> int:
    from app.tasks.ui_automation.run_ui_case import enqueue_run_ui_case

    db = deps_mod.SessionLocal()
    triggered = 0
    try:
        now = utc_now()
        rows = db.query(UiSchedule).filter(UiSchedule.enabled.is_(True)).all()
        for sch in rows:
            nxt = parse_iso(sch.next_run_at)
            if nxt is not None and nxt > now:
                continue
            suite = (
                db.query(UiSuite)
                .filter(UiSuite.id == sch.suite_id, UiSuite.project_id == sch.project_id)
                .first()
            )
            if suite is None:
                log.warning("ui schedule %s suite missing", sch.id)
                continue
            env_id = sch.env_id if sch.env_id is not None else suite.env_id
            browsers = suite.browsers_json if isinstance(suite.browsers_json, list) and suite.browsers_json else ["chromium"]
            for browser in browsers:
                run = UiRun(
                    project_id=sch.project_id,
                    trigger_type="suite",
                    target_id=suite.id,
                    env_id=env_id,
                    browser=str(browser),
                    status="pending",
                    triggered_by=sch.creator_id,
                    summary_json={"trigger": "schedule", "schedule_id": sch.id},
                    queue_name="ui",
                )
                db.add(run)
                db.commit()
                db.refresh(run)
                try:
                    task_id = enqueue_run_ui_case(run.id, queue=run.queue_name or "ui")
                    if task_id:
                        run.celery_task_id = task_id
                        db.commit()
                except Exception:
                    log.exception("scheduled ui enqueue failed schedule=%s run=%s", sch.id, run.id)
                    try:
                        execute_ui_run(db, run.id)
                    except Exception:
                        log.exception("scheduled ui sync failed schedule=%s run=%s", sch.id, run.id)
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
