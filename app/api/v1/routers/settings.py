from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.settings import OrgSettingsResponse, OrgSettingsUpdate
from app.services import settings as settings_service

router = APIRouter(prefix="/org/settings", tags=["org-settings"])


@router.get("", response_model=OrgSettingsResponse, summary="Get organization settings")
async def get_org_settings(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ORG_SETTINGS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OrgSettingsResponse:
    settings = await settings_service.get_org_settings(db, ctx)
    return OrgSettingsResponse.model_validate(settings)


@router.put("", response_model=OrgSettingsResponse, summary="Update organization settings")
async def update_org_settings(
    payload: OrgSettingsUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ORG_SETTINGS_MANAGE)),
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
    return OrgSettingsResponse.model_validate(settings)
