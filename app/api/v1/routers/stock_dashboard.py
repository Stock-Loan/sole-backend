from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.stock import StockDashboardSummary
from app.services import stock_dashboard

router = APIRouter(prefix="/org/dashboard", tags=["stock-dashboard"])


@router.get(
    "/stock-summary",
    response_model=StockDashboardSummary,
    summary="Get org-level stock dashboard summary",
)
async def get_stock_dashboard_summary(
    as_of: date | None = Query(default=None, description="Compute summary as of this date"),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.STOCK_DASHBOARD_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> StockDashboardSummary:
    return await stock_dashboard.build_dashboard_summary(db, ctx, as_of or date.today())
