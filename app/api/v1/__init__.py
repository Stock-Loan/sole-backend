from fastapi import APIRouter

from app.api.v1.routers import (
    auth,
    health,
    meta,
    onboarding,
    roles,
    acls,
    departments,
    announcements,
    settings,
    self as self_router,
    stock_grants,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(meta.router)
api_router.include_router(onboarding.router)
api_router.include_router(roles.router)
api_router.include_router(acls.router)
api_router.include_router(departments.router)
api_router.include_router(announcements.router)
api_router.include_router(settings.router)
api_router.include_router(self_router.router)
api_router.include_router(stock_grants.router)

__all__ = ["api_router"]
