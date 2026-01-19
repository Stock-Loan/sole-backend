from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.stock import StockGrantListResponse
from app.services import stock_grants

router = APIRouter(prefix="/me", tags=["stock-self"])


@router.get(
    "/stock/grants",
    response_model=StockGrantListResponse,
    summary="List stock grants for the current user",
)
async def list_my_stock_grants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(deps.require_permission(PermissionCode.STOCK_SELF_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> StockGrantListResponse:
    stmt = select(OrgMembership).where(
        OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    offset = (page - 1) * page_size
    grants, total = await stock_grants.list_grants(
        db, ctx, membership.id, offset=offset, limit=page_size
    )
    return StockGrantListResponse(items=grants, total=total)
