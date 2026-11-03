"""Celery task for conversational UI scenario agent explore."""

from __future__ import annotations

import logging

import app.deps as deps_mod
from app.services.ui_automation.ui_scenario_agent import (
    execute_session_explore,
    execute_session_trial_run,
)
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_ui_scenario_agent")
def run_ui_scenario_agent_task(session_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        execute_session_explore(db, session_id)
    except Exception:
        log.exception("run_ui_scenario_agent_task failed session_id=%s", session_id)
        raise
    finally:
        db.close()


def enqueue_ui_scenario_agent(session_id: int, queue: str = "ui") -> str | None:
    async_result = run_ui_scenario_agent_task.apply_async(args=[session_id], queue=queue or "ui")
    return getattr(async_result, "id", None)


@celery_app.task(name="run_ui_scenario_agent_trial")
def run_ui_scenario_agent_trial_task(session_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        execute_session_trial_run(db, session_id)
    except Exception:
        log.exception("run_ui_scenario_agent_trial_task failed session_id=%s", session_id)
        raise
    finally:
        db.close()


def enqueue_ui_scenario_agent_trial(session_id: int, queue: str = "ui") -> str | None:
    async_result = run_ui_scenario_agent_trial_task.apply_async(
        args=[session_id], queue=queue or "ui"
    )
    return getattr(async_result, "id", None)
