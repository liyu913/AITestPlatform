import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import HTTPException

from app.api.ai import routers as ai_routers
from app.api.api_automation import routers as api_automation_routers
from app.api.app_automation import routers as app_automation_routers
from app.api.functional import routers as functional_routers
from app.api.identity import routers as identity_routers
from app.api.knowledge import routers as knowledge_routers
from app.api.perf import routers as perf_routers
from app.api.project import routers as project_routers
from app.api.ui_automation import routers as ui_automation_routers
from app.bootstrap import ensure_default_admin
from app.config import settings
from app.errors import AppError, app_error_handler, http_exception_handler
from app.middleware.request_id import RequestIdMiddleware

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        ensure_default_admin(reset_password=False)
    except Exception:
        log.exception("Failed to ensure default admin on startup (DB may not be ready)")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

for routers in (
    identity_routers,
    project_routers,
    knowledge_routers,
    functional_routers,
    api_automation_routers,
    ui_automation_routers,
    app_automation_routers,
    perf_routers,
    ai_routers,
):
    for router in routers:
        app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
