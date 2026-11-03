"""Celery task for UI AI explore jobs."""

from __future__ import annotations

import logging

import app.deps as deps_mod
from app.services.ui_automation.ui_ai_explorer import execute_explore_job
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_ui_ai_explore")
def run_ui_ai_explore_task(job_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        execute_explore_job(db, job_id)
    except Exception:
        log.exception("run_ui_ai_explore_task failed job_id=%s", job_id)
        raise
    finally:
        db.close()


def enqueue_ui_ai_explore(job_id: int, queue: str = "ui") -> str | None:
    async_result = run_ui_ai_explore_task.apply_async(args=[job_id], queue=queue or "ui")
    return getattr(async_result, "id", None)
