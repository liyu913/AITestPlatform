from app.api.ui_automation.ui_ai import router as ui_ai_router
from app.api.ui_automation.ui_cases import router as ui_cases_router
from app.api.ui_automation.ui_elements import router as ui_elements_router
from app.api.ui_automation.ui_envs import router as ui_envs_router
from app.api.ui_automation.ui_recordings import router as ui_recordings_router
from app.api.ui_automation.ui_runs import router as ui_runs_router
from app.api.ui_automation.ui_scenario_agent import router as ui_scenario_agent_router
from app.api.ui_automation.ui_schedules import router as ui_schedules_router
from app.api.ui_automation.ui_suites import router as ui_suites_router

routers = [
    ui_envs_router,
    ui_cases_router,
    ui_recordings_router,
    ui_suites_router,
    ui_elements_router,
    ui_schedules_router,
    ui_runs_router,
    ui_ai_router,
    ui_scenario_agent_router,
]
