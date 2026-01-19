from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.schemas.self_dashboard import SelfDashboardSummary
from app.services import self_dashboard


router = APIRouter(prefix="/me/dashboard", tags=["self-dashboard"])


@router.get(
    "/summary",
    response_model=SelfDashboardSummary,
    summary="Get dashboard summary for the current user",
)
async def get_self_dashboard_summary(
    as_of: date | None = Query(default=None, description="Compute summary as of this date"),
    current_user=Depends(deps.require_permission(PermissionCode.STOCK_SELF_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> SelfDashboardSummary:
    try:
        return await self_dashboard.build_self_dashboard_summary(
            db,
            ctx,
            current_user.id,
            as_of or date.today(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
