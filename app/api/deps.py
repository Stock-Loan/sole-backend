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
from app.services import authz, settings as settings_service
from app.core.settings import settings
from app.db.session import get_db
from app.models import Org, User


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


def _extract_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header:
        return None
    prefix = "bearer "
    if auth_header.lower().startswith(prefix):
        return auth_header[len(prefix) :].strip()
    return None


async def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


async def get_tenant_context(
    request: Request,
    org_id_header: str | None = Header(default=None, alias="X-Org-Id"),
    legacy_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db_session),
) -> TenantContext:
    mode = settings.tenancy_mode
    if mode == "multi":
        token_org = None
        token_is_superuser = False
        token = _extract_bearer_token(request)
        if token:
            try:
                payload = decode_token(token)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
            token_org = payload.get("org")
            token_is_superuser = bool(payload.get("su"))

        header_org = org_id_header or legacy_tenant_id
        if token_org:
            if header_org and header_org != token_org:
                if not token_is_superuser:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Tenant header does not match token",
                    )
                candidate = header_org
            else:
                candidate = token_org
        else:
            candidate = header_org

        if not candidate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant resolution failed: provide X-Org-Id header",
            )
        org_stmt = select(Org.id).where(Org.id == candidate)
        if (await db.execute(org_stmt)).scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        set_tenant_id(candidate)
        return TenantContext(org_id=candidate)

    default_org = settings.default_org_id
    set_tenant_id(default_org)
    return TenantContext(org_id=default_org)


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
    token_org = payload.get("org")
    token_is_superuser = bool(payload.get("su"))
    if not user_sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if settings.tenancy_mode == "multi":
        if not token_org:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing org claim")
        if token_org != ctx.org_id and not token_is_superuser:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch")

    if token_is_superuser and token_org != ctx.org_id:
        stmt = select(User).where(User.id == user_sub)
    else:
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


async def _require_mfa_for_request(
    request: Request,
    current_user: User,
    ctx: TenantContext,
    db: AsyncSession,
    *,
    action: str | None = None,
) -> None:
    org_settings = await settings_service.get_org_settings(db, ctx)
    if not settings_service.is_mfa_action_required(org_settings, action):
        return
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        payload = decode_token(token, expected_type="access")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if not payload.get("mfa"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA required")


def require_permission_with_mfa(
    permission_code: PermissionCode | str,
    resource_type: str | None = None,
    resource_id_param: str | None = None,
    *,
    action: str | None = None,
):
    async def dependency(
        request: Request,
        current_user: User = Depends(require_permission(permission_code, resource_type, resource_id_param)),
        ctx: TenantContext = Depends(get_tenant_context),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:
        await _require_mfa_for_request(request, current_user, ctx, db, action=action)
        return current_user

    return dependency


async def require_mfa_for_action(
    request: Request,
    current_user: User,
    ctx: TenantContext,
    db: AsyncSession,
    action: str,
) -> None:
    await _require_mfa_for_request(request, current_user, ctx, db, action=action)


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
