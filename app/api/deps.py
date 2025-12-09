from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.settings import settings
from app.db.session import get_db


@dataclass(slots=True)
class TenantContext:
    org_id: str


def _resolve_subdomain(request: Request) -> str | None:
    host = request.headers.get("host", "")
    # strip port if present
    host = host.split(":")[0]
    parts = host.split(".")
    # ignore localhost/invalid hosts
    if len(parts) >= 3:
        return parts[0]
    return None


async def get_tenant_context(
    request: Request,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> TenantContext:
    mode = settings.tenancy_mode
    if mode == "multi":
        candidate = tenant_id or _resolve_subdomain(request)
        if not candidate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant resolution failed: provide X-Tenant-ID header or subdomain",
            )
        set_tenant_id(candidate)
        return TenantContext(org_id=candidate)

    default_org = settings.default_org_id
    set_tenant_id(default_org)
    return TenantContext(org_id=default_org)


async def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db
