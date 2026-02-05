import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.security import decode_token, create_step_up_challenge_token, decode_step_up_token
from app.core.permissions import PermissionCode
from app.services import authz, settings as settings_service
from app.core.settings import settings
from app.db.session import get_db
from app.models import Org, OrgMembership, User

logger = logging.getLogger(__name__)


class StepUpMfaRequired(Exception):
    """Exception raised when step-up MFA is required for an action."""

    def __init__(self, challenge_token: str, action: str):
        self.challenge_token = challenge_token
        self.action = action
        super().__init__("Step-up MFA required")


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
        token_user_id = None
        token = _extract_bearer_token(request)
        if token:
            try:
                payload = decode_token(token)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
                ) from exc
            token_org = payload.get("org")
            token_is_superuser = bool(payload.get("su"))
            token_user_id = payload.get("sub")

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
        if token_user_id and not token_is_superuser:
            membership = await get_membership(db, user_id=token_user_id, org_id=candidate)
            if not membership:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User is not a member of this organization",
                )
        set_tenant_id(candidate)
        return TenantContext(org_id=candidate)

    default_org = settings.default_org_id
    header_org = org_id_header or legacy_tenant_id
    if header_org and header_org != default_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant header does not match default org",
        )
    set_tenant_id(default_org)
    return TenantContext(org_id=default_org)


async def get_membership(
    db: AsyncSession,
    *,
    user_id: str,
    org_id: str,
) -> OrgMembership | None:
    stmt = select(OrgMembership).where(
        OrgMembership.org_id == org_id,
        OrgMembership.user_id == user_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def membership_allows_auth(membership: OrgMembership, *, allow_pending: bool) -> bool:
    employment = (membership.employment_status or "").upper()
    platform = (membership.platform_status or "").upper()
    invitation = (membership.invitation_status or "").upper()

    if employment and employment != "ACTIVE":
        return False
    if allow_pending:
        if platform not in {"ACTIVE", "INVITED"}:
            return False
        if invitation not in {"PENDING", "ACCEPTED"}:
            return False
        return True
    if platform != "ACTIVE":
        return False
    if invitation != "ACCEPTED":
        return False
    return True


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
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing org claim"
            )
        if token_org != ctx.org_id and not token_is_superuser:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch"
            )
    if token_org and token_org != ctx.org_id and not token_is_superuser:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch")

    stmt = select(User).where(User.id == user_sub)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    if token_version is not None and user.token_version != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    if not token_is_superuser:
        membership = await get_membership(db, user_id=user.id, org_id=ctx.org_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not a member of org"
            )
        if not membership_allows_auth(membership, allow_pending=allow_password_change):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Membership is not active"
            )

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


def _extract_step_up_token(request: Request) -> str | None:
    """Extract step-up token from X-Step-Up-Token header."""
    return request.headers.get("X-Step-Up-Token")


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

    # For action-level MFA, check for step-up token first
    if action is not None:
        step_up_token = _extract_step_up_token(request)
        if step_up_token:
            try:
                step_up_payload = decode_step_up_token(step_up_token)
                # Verify the step-up token matches this user, org, and action
                if (
                    step_up_payload.get("sub") == str(current_user.id)
                    and step_up_payload.get("org") == ctx.org_id
                    and step_up_payload.get("action") == action
                ):
                    return  # Step-up MFA already completed
            except ValueError:
                pass  # Invalid step-up token, continue to require new challenge

        # No valid step-up token, create a challenge and raise exception
        challenge_token = create_step_up_challenge_token(
            str(current_user.id),
            ctx.org_id,
            action,
        )
        raise StepUpMfaRequired(challenge_token=challenge_token, action=action)

    # For general MFA requirement (not action-specific), check token claims
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        payload = decode_token(token, expected_type="access")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    logger.info(f"Token claims: mfa={payload.get('mfa')}, mfa_method={payload.get('mfa_method')}")
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
        current_user: User = Depends(
            require_permission(permission_code, resource_type, resource_id_param)
        ),
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


def require_permission(
    permission_code: PermissionCode | str,
    resource_type: str | None = None,
    resource_id_param: str | None = None,
):
    async def dependency(
        request: Request,
        current_user: User = Depends(require_authenticated_user),
        ctx: TenantContext = Depends(get_tenant_context),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:
        if not ctx.org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant context missing"
            )
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
            target = (
                permission_code.value
                if isinstance(permission_code, PermissionCode)
                else str(permission_code)
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {target}",
            )
        return current_user

    return dependency
