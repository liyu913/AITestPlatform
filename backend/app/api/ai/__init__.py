from app.api.ai.admin_ai_call_logs import router as admin_ai_call_logs_router
from app.api.ai.admin_ai_settings import router as admin_ai_router
from app.api.ai.ai_generate import router as ai_generate_router
from app.api.ai.ai_sessions import router as ai_sessions_router

routers = [admin_ai_router, admin_ai_call_logs_router, ai_generate_router, ai_sessions_router]
