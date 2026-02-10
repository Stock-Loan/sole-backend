import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import set_tenant_id
from app.core.tenant import normalize_org_id
from app.core.security import (
    decode_token,
    decode_pre_org_token,
    create_step_up_challenge_token,
    decode_step_up_token,
)
from app.core.permissions import PermissionCode
from app.services import authz, settings as settings_service
from app.core.settings import settings
from app.db.session import get_db
from app.models import Identity, Org, OrgMembership, User

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


def _refresh_paths_match(path: str) -> bool:
    return path.endswith("/auth/refresh") or path.endswith("/auth/refresh/csrf")


def _decode_bearer_payload_cached(request: Request) -> dict | None:
    cached = getattr(request.state, "_decoded_access_payload", None)
    if cached is not None:
        return cached
    token = _extract_bearer_token(request)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    request.state._decoded_access_payload = payload
    return payload


def _org_from_refresh_cookie(request: Request) -> str | None:
    refresh_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if not refresh_token:
        return None
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return payload.get("org")


async def get_db_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


async def get_current_identity(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Identity:
    """Extract identity from pre_org_token or access_token.

    Used for pre-org endpoints (login flow) and any endpoint that
    needs to operate on the global identity record.
    """
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )

    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    token_type = payload.get("type")

    if token_type == "pre_org":
        identity_id = payload.get("sub")
        if not identity_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid pre-org token"
            )
        identity = await db.get(Identity, identity_id)
        if not identity or not identity.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identity not found or inactive",
            )
        return identity

    if token_type == "access":
        identity_id = payload.get("iid")
        if not identity_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing identity claim",
            )
        identity = await db.get(Identity, identity_id)
        if not identity or not identity.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identity not found or inactive",
            )
        return identity

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unsupported token type for identity resolution",
    )


async def get_tenant_context(
    request: Request,
    org_id_header: str | None = Header(default=None, alias="X-Org-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> TenantContext:
    mode = settings.tenancy_mode
    if mode == "multi":
        token_org = None
        token_is_superuser = False
        token_user_id = None
        token_type = None
        payload = _decode_bearer_payload_cached(request)
        if payload:
            token_org = payload.get("org")
            token_is_superuser = bool(payload.get("su"))
            token_user_id = payload.get("sub")
            token_type = payload.get("type")

        # pre_org tokens have no org claim — allow endpoints to bypass
        # org resolution when they only need the identity.
        if token_type == "pre_org":
            # Pre-org endpoints should not require tenant context.
            # If the caller specifically provided an org header, honour it;
            # otherwise raise so that the endpoint can use get_current_identity
            # instead.
            header_org = org_id_header
            if not header_org:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Pre-org token: provide X-Org-Id or use identity-only endpoints",
                )
            # Fall through — validate the provided header_org normally.
            candidate = header_org
        else:
            header_org = org_id_header
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

            # Subdomain-based tenant resolution (e.g. acme.example.com → acme)
            if not candidate:
                host = request.headers.get("host", "")
                hostname = host.split(":")[0]  # strip port
                parts = hostname.split(".")
                if len(parts) >= 3:
                    subdomain = parts[0]
                    if (
                        not settings.allowed_tenant_hosts
                        or hostname in settings.allowed_tenant_hosts
                    ):
                        candidate = subdomain

            if not candidate and _refresh_paths_match(request.url.path):
                candidate = _org_from_refresh_cookie(request)

        if not candidate:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant resolution failed: provide X-Org-Id header",
            )
        try:
            candidate = normalize_org_id(candidate)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tenant id format: {exc}",
            ) from exc
        org_stmt = select(Org.id).where(Org.id == candidate)
        if (await db.execute(org_stmt)).scalar_one_or_none() is None:
            logger.warning("Tenant resolution failed: org %r not found in database", candidate)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Org not found: {candidate}")
        if token_user_id and not token_is_superuser and token_type != "pre_org":
            membership = await get_membership(db, user_id=token_user_id, org_id=candidate)
            if not membership:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User is not a member of this organization",
                )
        set_tenant_id(candidate)
        return TenantContext(org_id=candidate)

    default_org = settings.default_org_id
    try:
        default_org = normalize_org_id(default_org)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Default tenant configuration is invalid: {exc}",
        ) from exc
    header_org = org_id_header
    if header_org and header_org != default_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant header does not match default org",
        )
    org_stmt = select(Org.id).where(Org.id == default_org)
    if (await db.execute(org_stmt)).scalar_one_or_none() is None:
        logger.warning("Tenant resolution failed: default org %r not found in database", default_org)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Org not found: {default_org}")
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


async def get_membership_by_id(
    db: AsyncSession,
    *,
    membership_id: str,
    org_id: str,
) -> OrgMembership | None:
    stmt = select(OrgMembership).where(
        OrgMembership.id == membership_id,
        OrgMembership.org_id == org_id,
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
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
    ctx: TenantContext = Depends(get_tenant_context),
) -> User:
    return await _get_current_user(request, token, db, ctx, allow_password_change=False)


async def get_current_user_allow_password_change(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
    ctx: TenantContext = Depends(get_tenant_context),
) -> User:
    return await _get_current_user(request, token, db, ctx, allow_password_change=True)


async def _get_current_user(
    request: Request,
    token: str,
    db: AsyncSession,
    ctx: TenantContext,
    allow_password_change: bool,
) -> User:
    payload = _decode_bearer_payload_cached(request)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unexpected token type",
        )
    user_sub = payload.get("sub")
    token_version = payload.get("tv")
    token_org = payload.get("org")
    identity_id = payload.get("iid")
    token_is_superuser = bool(payload.get("su"))
    impersonator_user_id = payload.get("imp")
    impersonator_identity_id = payload.get("imp_iid")
    if not user_sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not token_org:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing org claim"
        )
    if token_org != ctx.org_id and not token_is_superuser:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch"
        )

    # When impersonating, validate against the *impersonator*'s identity for
    # session revocation, then load and return the *target* user.
    if impersonator_user_id and impersonator_identity_id:
        # Validate impersonator identity is still active / not revoked
        imp_identity = await db.get(Identity, impersonator_identity_id)
        if not imp_identity or not imp_identity.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impersonator identity inactive",
            )
        if token_version is not None and imp_identity.token_version != token_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impersonation session revoked",
            )
        now = datetime.now(timezone.utc)
        enforce_inactivity(imp_identity.last_active_at, now)
        is_background = request.headers.get("X-Background-Request", "").lower() in {"1", "true"}
        if not is_background:
            imp_identity.last_active_at = now
            db.add(imp_identity)
            await db.commit()

        # Load the target user being impersonated
        stmt = select(User).options(selectinload(User.identity)).where(User.id == user_sub)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Impersonated user not found or inactive",
            )

        # Validate the target user still has an active membership.
        # The membership may have been revoked after impersonation started.
        if not user.is_superuser:
            membership = await get_membership(db, user_id=user.id, org_id=ctx.org_id)
            if not membership:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Impersonated user is not a member of org",
                )
            if not membership_allows_auth(membership, allow_pending=False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Impersonated user membership is not active",
                )

        # Tag the user object with impersonation metadata for audit logging
        user._impersonator_user_id = impersonator_user_id  # type: ignore[attr-defined]
        user._impersonator_identity_id = impersonator_identity_id  # type: ignore[attr-defined]
        user._is_impersonated = True  # type: ignore[attr-defined]
        return user

    # ── Normal (non-impersonation) flow ──
    stmt = select(User).options(selectinload(User.identity)).where(User.id == user_sub)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    # Validate token_version against identity (global session revocation)
    identity = user.identity
    if not identity or not identity.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identity inactive"
        )
    if token_version is not None and identity.token_version != token_version:
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
    enforce_inactivity(identity.last_active_at, now)
    # Only update last_active_at for user-initiated requests.
    # Background polling (marked with X-Background-Request header) should not
    # reset the inactivity timer to ensure the timeout is meaningful.
    is_background = request.headers.get("X-Background-Request", "").lower() in {"1", "true"}
    if not is_background:
        identity.last_active_at = now
        db.add(identity)
        await db.commit()
    if identity.must_change_password and not allow_password_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    user._is_impersonated = False  # type: ignore[attr-defined]
    return user


async def require_authenticated_user(current_user: User = Depends(get_current_user)) -> User:
    """Simple guard to require an authenticated user (no permission checks)."""
    return current_user


def reject_during_impersonation(
    current_user: User,
    *,
    detail: str = "This action is not allowed during impersonation",
) -> None:
    """Raise 403 if the current session is an impersonation session.

    Use this guard at the top of endpoints that modify the target user's
    credentials, MFA state, or session (password change, MFA reset, logout,
    etc.) to prevent an impersonator from tampering with the real user's
    account.
    """
    if getattr(current_user, "_is_impersonated", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def extract_step_up_token(request: Request) -> str | None:
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
        step_up_token = extract_step_up_token(request)
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
    logger.debug(f"Token claims: mfa={payload.get('mfa')}, mfa_method={payload.get('mfa_method')}")
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
