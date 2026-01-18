from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.schemas.users import UserDashboardSummary
from app.services import user_dashboard


router = APIRouter(prefix="/org/dashboard", tags=["user-dashboard"])


@router.get(
    "/user-summary",
    response_model=UserDashboardSummary,
    summary="Get org user dashboard summary",
)
async def get_user_dashboard_summary(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.USER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserDashboardSummary:
    return await user_dashboard.build_dashboard_summary(db, ctx)
