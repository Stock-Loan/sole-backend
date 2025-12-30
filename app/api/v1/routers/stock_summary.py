from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.stock import StockSummaryResponse
from app.services import stock_summary

router = APIRouter(prefix="/org", tags=["stock-summary"])


@router.get(
    "/users/{membership_id}/stock/summary",
    response_model=StockSummaryResponse,
    summary="Get stock summary for a membership",
)
async def get_stock_summary(
    membership_id: UUID,
    as_of: date | None = Query(default=None, description="Compute summary as of this date"),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.STOCK_VESTING_VIEW)),
    __: User = Depends(deps.require_permission(PermissionCode.STOCK_ELIGIBILITY_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> StockSummaryResponse:
    try:
        summary = await stock_summary.build_stock_summary(
            db,
            ctx,
            membership_id,
            as_of or date.today(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return summary
