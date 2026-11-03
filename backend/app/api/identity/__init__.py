from app.api.identity.admin_users import router as admin_users_router
from app.api.identity.auth import router as auth_router
from app.api.identity.users import router as users_router

routers = [auth_router, users_router, admin_users_router]
