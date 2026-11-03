from __future__ import annotations

import logging

import app.deps as deps_mod
from app.services.api_automation.api_run_executor import execute_api_run
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_api_case")
def run_api_case_task(run_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        execute_api_run(db, run_id)
    except Exception:
        log.exception("run_api_case_task failed run_id=%s", run_id)
        raise
    finally:
        db.close()


def enqueue_run_api_case(run_id: int) -> str | None:
    async_result = run_api_case_task.delay(run_id)
    return getattr(async_result, "id", None)
