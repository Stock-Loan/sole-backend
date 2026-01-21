from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.loan import LoanQuoteRequest, LoanQuoteResponse
from app.services import loan_quotes

router = APIRouter(prefix="/me/loan-applications", tags=["loan-applications"])


@router.post(
    "/quote",
    response_model=LoanQuoteResponse,
    summary="Get a loan quote for exercising stock options",
)
async def get_loan_quote(
    payload: LoanQuoteRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.LOAN_APPLY)),
    db: AsyncSession = Depends(get_db),
) -> LoanQuoteResponse:
    membership_stmt = select(OrgMembership).where(
        OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id
    )
    membership_result = await db.execute(membership_stmt)
    membership = membership_result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    try:
        quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, payload)
        return quote
    except loan_quotes.LoanQuoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
