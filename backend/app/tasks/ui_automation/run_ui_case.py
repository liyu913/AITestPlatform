from __future__ import annotations

import logging

import app.deps as deps_mod
from app.services.functional.cycle_attempt_sync import sync_attempt_from_run
from app.services.ui_automation.ui_run_executor import execute_ui_run
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_ui_case", bind=True)
def run_ui_case_task(self, run_id: int) -> None:  # noqa: ANN001
    db = deps_mod.SessionLocal()
    try:
        execute_ui_run(db, run_id)
        # if deferred due to concurrency, requeue shortly
        from app.models.ui_automation.ui_automation import UiRun

        run = db.get(UiRun, run_id)
        if run and run.status == "pending":
            summary = run.summary_json if isinstance(run.summary_json, dict) else {}
            if summary.get("deferred"):
                run_ui_case_task.apply_async(args=[run_id], countdown=15, queue=run.queue_name or "ui")
        elif run is not None:
            # Mirror the outcome into the batch item (no-op when the run has none).
            try:
                sync_attempt_from_run(db, run_type="ui", run_id=run_id)
            except Exception:
                log.exception("sync ui attempt failed run_id=%s", run_id)
    except Exception:
        log.exception("run_ui_case_task failed run_id=%s", run_id)
        raise
    finally:
        db.close()


def enqueue_run_ui_case(run_id: int, queue: str = "ui") -> str | None:
    async_result = run_ui_case_task.apply_async(args=[run_id], queue=queue or "ui")
    return getattr(async_result, "id", None)
