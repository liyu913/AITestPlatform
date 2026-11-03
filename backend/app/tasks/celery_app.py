from celery import Celery

from app.config import settings

celery_app = Celery(
    "raina",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "tick-api-schedules-every-minute": {
            "task": "tick_api_schedules",
            "schedule": 60.0,
        },
        "tick-ui-schedules-every-minute": {
            "task": "tick_ui_schedules",
            "schedule": 60.0,
        },
    },
)

# Register tasks
import app.tasks.knowledge.indexing_task  # noqa: E402
import app.tasks.knowledge.parse_and_index  # noqa: E402
import app.tasks.api_automation.run_api_case  # noqa: E402
import app.tasks.ui_automation.run_ui_case  # noqa: E402
import app.tasks.app_automation.run_app_case  # noqa: E402
import app.tasks.perf.run_perf_scenario  # noqa: E402
import app.tasks.api_automation.api_schedules  # noqa: E402
import app.tasks.ui_automation.ui_schedules  # noqa: E402
import app.tasks.ui_automation.run_ui_ai_explore  # noqa: E402
import app.tasks.ui_automation.run_ui_scenario_agent  # noqa: E402
