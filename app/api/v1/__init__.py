from fastapi import APIRouter

from app.api.v1.routers import auth, health, meta, onboarding

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(meta.router)
api_router.include_router(onboarding.router)

__all__ = ["api_router"]
