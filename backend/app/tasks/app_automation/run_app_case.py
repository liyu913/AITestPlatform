from __future__ import annotations

import logging

import app.deps as deps_mod
from app.services.app_automation.app_run_executor import execute_app_run
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_app_case", bind=True)
def run_app_case_task(self, run_id: int) -> None:  # noqa: ANN001
    db = deps_mod.SessionLocal()
    try:
        execute_app_run(db, run_id)
        from app.models.app_automation.app_automation import AppRun

        run = db.get(AppRun, run_id)
        if run and run.status == "pending":
            summary = run.summary_json if isinstance(run.summary_json, dict) else {}
            if summary.get("deferred"):
                run_app_case_task.apply_async(args=[run_id], countdown=15, queue=run.queue_name or "app")
    except Exception:
        log.exception("run_app_case_task failed run_id=%s", run_id)
        raise
    finally:
        db.close()


def enqueue_run_app_case(run_id: int, queue: str = "app") -> str | None:
    async_result = run_app_case_task.apply_async(args=[run_id], queue=queue or "app")
    return getattr(async_result, "id", None)
