from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.stock import (
    EmployeeStockGrantCreate,
    EmployeeStockGrantOut,
    EmployeeStockGrantUpdate,
    StockGrantPreviewResponse,
    StockGrantListResponse,
)
from app.schemas.settings import MfaEnforcementAction
from app.services import stock_grants

router = APIRouter(prefix="/org", tags=["stock-grants"])


@router.get(
    "/users/{membership_id}/stock/grants",
    response_model=StockGrantListResponse,
    summary="List stock grants for a membership",
)
async def list_grants(
    membership_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.STOCK_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> StockGrantListResponse:
    membership = await stock_grants.get_membership(db, ctx, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    offset = (page - 1) * page_size
    grants, total = await stock_grants.list_grants(db, ctx, membership_id, offset=offset, limit=page_size)
    return StockGrantListResponse(items=grants, total=total)


@router.post(
    "/users/{membership_id}/stock/grants/preview",
    response_model=StockGrantPreviewResponse,
    summary="Preview a stock grant and vesting schedule",
)
async def preview_grant(
    membership_id: UUID,
    payload: EmployeeStockGrantCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.STOCK_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> StockGrantPreviewResponse:
    membership = await stock_grants.get_membership(db, ctx, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    try:
        stock_grants._ensure_membership_active(membership)
        return stock_grants.preview_grant(payload, date.today())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/users/{membership_id}/stock/grants",
    response_model=EmployeeStockGrantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a stock grant for a membership",
)
async def create_grant(
    membership_id: UUID,
    payload: EmployeeStockGrantCreate,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.STOCK_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeStockGrantOut:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.STOCK_GRANT_ASSIGNMENT.value,
    )
    membership = await stock_grants.get_membership(db, ctx, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    try:
        grant = await stock_grants.create_grant(
            db,
            ctx,
            membership_id,
            payload,
            actor_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EmployeeStockGrantOut.model_validate(grant)


@router.get(
    "/stock/grants/{grant_id}",
    response_model=EmployeeStockGrantOut,
    summary="Get a stock grant by id",
)
async def get_grant(
    grant_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.STOCK_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeStockGrantOut:
    grant = await stock_grants.get_grant(db, ctx, grant_id)
    if not grant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    return EmployeeStockGrantOut.model_validate(grant)


@router.patch(
    "/stock/grants/{grant_id}",
    response_model=EmployeeStockGrantOut,
    summary="Update a stock grant",
)
async def update_grant(
    grant_id: UUID,
    payload: EmployeeStockGrantUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.STOCK_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> EmployeeStockGrantOut:
    grant = await stock_grants.get_grant(db, ctx, grant_id)
    if not grant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    try:
        updated = await stock_grants.update_grant(
            db,
            ctx,
            grant,
            payload,
            actor_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EmployeeStockGrantOut.model_validate(updated)
