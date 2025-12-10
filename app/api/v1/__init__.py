from fastapi import APIRouter, Depends

from app.api import deps
from app.api.v1.routers import auth, health

api_router = APIRouter(dependencies=[Depends(deps.get_tenant_context)])
api_router.include_router(health.router)
api_router.include_router(auth.router)

__all__ = ["api_router"]
