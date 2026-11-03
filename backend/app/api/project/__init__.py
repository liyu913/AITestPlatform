from app.api.project.dashboard import router as dashboard_router
from app.api.project.modules import router as modules_router
from app.api.project.projects import router as projects_router

routers = [projects_router, dashboard_router, modules_router]
