from __future__ import annotations

import logging

import app.deps as deps_mod
from app.services.api_automation.api_run_executor import execute_api_run
from app.services.functional.cycle_attempt_sync import sync_attempt_from_run
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_api_case")
def run_api_case_task(run_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        execute_api_run(db, run_id)
        # Mirror the outcome into the batch item (no-op when the run has none).
        try:
            sync_attempt_from_run(db, run_type="api", run_id=run_id)
        except Exception:
            log.exception("sync api attempt failed run_id=%s", run_id)
    except Exception:
        log.exception("run_api_case_task failed run_id=%s", run_id)
        raise
    finally:
        db.close()


def enqueue_run_api_case(run_id: int) -> str | None:
    async_result = run_api_case_task.delay(run_id)
    return getattr(async_result, "id", None)
