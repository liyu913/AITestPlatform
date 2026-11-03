from app.api.perf.perf_runs import router as perf_runs_router
from app.api.perf.perf_scenarios import router as perf_scenarios_router

routers = [perf_scenarios_router, perf_runs_router]
