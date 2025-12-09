from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.settings import settings
from app.db.session import get_db


@dataclass(slots=True)
class TenantContext:
    org_id: str


async def get_tenant_context(tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> TenantContext:
    mode = settings.tenancy_mode
    if mode == "multi":
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Tenant-ID header required for multi-tenant mode",
            )
        set_tenant_id(tenant_id)
        return TenantContext(org_id=tenant_id)

    default_org = "default"
    set_tenant_id(default_org)
    return TenantContext(org_id=default_org)


async def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db
