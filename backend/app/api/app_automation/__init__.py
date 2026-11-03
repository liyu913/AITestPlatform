from app.api.app_automation.app_cases import router as app_cases_router
from app.api.app_automation.app_devices import router as app_devices_router
from app.api.app_automation.app_elements import router as app_elements_router
from app.api.app_automation.app_envs import router as app_envs_router
from app.api.app_automation.app_runs import router as app_runs_router

routers = [
    app_envs_router,
    app_cases_router,
    app_elements_router,
    app_devices_router,
    app_runs_router,
]
