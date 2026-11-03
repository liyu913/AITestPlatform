from app.api.api_automation.api_case_agent import router as api_case_agent_router
from app.api.api_automation.api_case_edit_agent import router as api_case_edit_agent_router
from app.api.api_automation.api_cases import router as api_cases_router
from app.api.api_automation.api_definitions import router as api_definitions_router
from app.api.api_automation.api_envs import router as api_envs_router
from app.api.api_automation.api_postman import router as api_postman_router
from app.api.api_automation.api_runs import router as api_runs_router
from app.api.api_automation.api_scenario_agent import router as api_scenario_agent_router
from app.api.api_automation.api_schedules import router as api_schedules_router
from app.api.api_automation.api_specs import router as api_specs_router
from app.api.api_automation.api_suites import router as api_suites_router

routers = [
    api_envs_router,
    api_definitions_router,
    api_cases_router,
    api_suites_router,
    api_scenario_agent_router,
    api_case_agent_router,
    api_case_edit_agent_router,
    api_specs_router,
    api_schedules_router,
    api_postman_router,
    api_runs_router,
]
