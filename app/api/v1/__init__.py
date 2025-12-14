from fastapi import APIRouter

from app.api.v1.routers import auth, health, meta, onboarding, roles

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(meta.router)
api_router.include_router(onboarding.router)
api_router.include_router(roles.router)

__all__ = ["api_router"]
