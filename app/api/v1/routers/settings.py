from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.pbgc_rates import PbgcMidTermRateEntry, PbgcRateRefreshResponse
from app.schemas.settings import OrgSettingsResponse, OrgSettingsUpdate
from app.services import pbgc_rates, settings as settings_service

router = APIRouter(prefix="/org/settings", tags=["org-settings"])


@router.get("", response_model=OrgSettingsResponse, summary="Get organization settings")
async def get_org_settings(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ORG_SETTINGS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsResponse:
    settings = await settings_service.get_org_settings(db, ctx)
    latest_rate = await pbgc_rates.get_latest_annual_rate(db)
    response = OrgSettingsResponse.model_validate(settings)
    if latest_rate is not None:
        response = response.model_copy(
            update={"variable_base_rate_annual_percent": latest_rate}
        )
    return response


@router.put("", response_model=OrgSettingsResponse, summary="Update organization settings")
async def update_org_settings(
    payload: OrgSettingsUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission_with_mfa(PermissionCode.ORG_SETTINGS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsResponse:
    try:
        settings = await settings_service.update_org_settings(
            db,
            ctx,
            payload,
            actor_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    latest_rate = await pbgc_rates.get_latest_annual_rate(db)
    response = OrgSettingsResponse.model_validate(settings)
    if latest_rate is not None:
        response = response.model_copy(
            update={"variable_base_rate_annual_percent": latest_rate}
        )
    return response


@router.post(
    "/pbgc-rates/refresh",
    response_model=PbgcRateRefreshResponse,
    summary="Refresh PBGC mid-term rates",
)
async def refresh_pbgc_rates(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ORG_SETTINGS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> PbgcRateRefreshResponse:
    _ = ctx
    updated_rows, fetched_at = await pbgc_rates.upsert_current_year_rates(db)
    return PbgcRateRefreshResponse(updated_rows=updated_rows, fetched_at=fetched_at)


@router.get(
    "/pbgc-rates",
    response_model=list[PbgcMidTermRateEntry],
    summary="List PBGC mid-term rates",
)
async def list_pbgc_rates(
    year: int | None = None,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ORG_SETTINGS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[PbgcMidTermRateEntry]:
    _ = ctx
    rows = await pbgc_rates.list_rates(db, year=year)
    return [PbgcMidTermRateEntry.model_validate(row) for row in rows]
