import logging

from app import deps as deps_mod
from app.services.perf.perf_run_executor import execute_perf_run
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="run_perf_scenario")
def run_perf_scenario_task(run_id: int) -> None:
    db = deps_mod.SessionLocal()
    try:
        execute_perf_run(db, run_id)
    except Exception:
        log.exception("run_perf_scenario_task failed run_id=%s", run_id)
        raise
    finally:
        db.close()


def enqueue_run_perf_scenario(run_id: int) -> str | None:
    async_result = run_perf_scenario_task.delay(run_id)
    return getattr(async_result, "id", None)
