from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.loan import LoanDashboardSummary
from app.services import loan_dashboard

router = APIRouter(prefix="/org/dashboard", tags=["loan-dashboard"])


@router.get(
    "/loan-summary",
    response_model=LoanDashboardSummary,
    summary="Get org loan dashboard summary",
)
async def get_loan_dashboard_summary(
    as_of: date | None = Query(default=None, description="Compute summary as of this date"),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.LOAN_DASHBOARD_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> LoanDashboardSummary:
    return await loan_dashboard.build_dashboard_summary(db, ctx, as_of)
