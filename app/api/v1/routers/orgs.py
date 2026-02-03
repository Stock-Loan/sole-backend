from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.settings import settings
from app.db.session import get_db
from app.schemas.orgs import OrgCreateRequest, OrgDTO
from app.services import orgs as org_service


router = APIRouter(prefix="/orgs", tags=["orgs"])


@router.post(
    "",
    response_model=OrgDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Create organization (super admin only)",
)
async def create_org(
    payload: OrgCreateRequest,
    current_user=Depends(deps.get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgDTO:
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin required")
    if settings.tenancy_mode != "multi":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Org creation is disabled in single-tenant mode",
        )
    try:
        org = await org_service.create_org(db, payload=payload, creator=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OrgDTO.model_validate(org)
