from app.api.project.dashboard import router as dashboard_router
from app.api.project.evidence import router as evidence_router
from app.api.project.executions import router as executions_router
from app.api.project.modules import router as modules_router
from app.api.project.projects import router as projects_router
from app.api.project.search import router as search_router
from app.api.project.workbench import router as workbench_router

routers = [
    projects_router,
    dashboard_router,
    workbench_router,
    executions_router,
    evidence_router,
    search_router,
    modules_router,
]
