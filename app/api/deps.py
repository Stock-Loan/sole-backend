from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.security import decode_token
from app.core.permissions import PermissionCode
from app.services import authz
from app.core.settings import settings
from app.db.session import get_db
from app.models import User


@dataclass(slots=True)
class TenantContext:
    org_id: str


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def enforce_inactivity(last_active_at: Optional[datetime], now: datetime) -> None:
    timeout = timedelta(minutes=settings.session_timeout_minutes)
    if last_active_at and now - last_active_at > timeout:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to inactivity",
        )


def _resolve_subdomain(request: Request) -> str | None:
    host = request.headers.get("host", "")
    # strip port if present
    host = host.split(":")[0]
    if settings.allowed_tenant_hosts:
        if host not in settings.allowed_tenant_hosts:
            return None
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


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
    ctx: TenantContext = Depends(get_tenant_context),
) -> User:
    return await _get_current_user(token, db, ctx, allow_password_change=False)


async def get_current_user_allow_password_change(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
    ctx: TenantContext = Depends(get_tenant_context),
) -> User:
    return await _get_current_user(token, db, ctx, allow_password_change=True)


async def _get_current_user(
    token: str,
    db: AsyncSession,
    ctx: TenantContext,
    allow_password_change: bool,
) -> User:
    try:
        payload = decode_token(token, expected_type="access")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    user_sub = payload.get("sub")
    token_version = payload.get("tv")
    if not user_sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    stmt = select(User).where(User.id == user_sub, User.org_id == ctx.org_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    if token_version is not None and user.token_version != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    now = datetime.now(timezone.utc)
    enforce_inactivity(user.last_active_at, now)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if user.must_change_password and not allow_password_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    return user


async def require_authenticated_user(current_user: User = Depends(get_current_user)) -> User:
    """Simple guard to require an authenticated user (no permission checks)."""
    return current_user


def require_permission(permission_code: PermissionCode | str, resource_type: str | None = None, resource_id_param: str | None = None):
    async def dependency(
        request: Request,
        current_user: User = Depends(require_authenticated_user),
        ctx: TenantContext = Depends(get_tenant_context),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:
        if not ctx.org_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant context missing")
        resource_id = None
        if resource_type and resource_id_param:
            resource_id = request.path_params.get(resource_id_param)
        allowed = await authz.check_permission(
            current_user,
            ctx,
            permission_code,
            db,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        if not allowed:
            target = permission_code.value if isinstance(permission_code, PermissionCode) else str(permission_code)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {target}",
            )
        return current_user

    return dependency
