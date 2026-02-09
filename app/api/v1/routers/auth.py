from datetime import datetime, timezone

import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.core.security import (
    create_access_token,
    create_mfa_challenge_token,
    create_mfa_setup_token,
    create_pre_org_token,
    create_refresh_token,
    create_step_up_token,
    decode_mfa_challenge_token,
    decode_mfa_setup_token,
    decode_step_up_challenge_token,
    decode_token,
    get_password_hash,
)
from app.core.settings import settings
from app.db.session import get_db
from app.models import Identity, OrgMembership, User
from app.models.org import Org
from app.schemas.auth import (
    AuthOrgsResponse,
    ChangePasswordRequest,
    CsrfTokenResponse,
    LoginRequest,
    LoginResponse,
    MfaEnrollStartRequest,
    MfaEnrollVerifyRequest,
    MfaSetupCompleteResponse,
    MfaSetupStartResponse,
    MfaSetupVerifyRequest,
    MfaVerifyRequest,
    MfaVerifyResponse,
    OrgSummary,
    RefreshRequest,
    SelectOrgRequest,
    SelectOrgResponse,
    StepUpVerifyRequest,
    StepUpVerifyResponse,
    TokenPair,
    UserOut,
)
from app.api.auth_utils import constant_time_verify, enforce_login_limits, record_login_attempt
from app.services import authz as authz_service, mfa as mfa_service, settings as settings_service
from app.utils.login_security import enforce_mfa_rate_limit, mark_refresh_used_atomic

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _set_refresh_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    if not settings.auth_refresh_cookie_enabled:
        return
    common_kwargs = {
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "path": settings.auth_cookie_path,
    }
    if settings.auth_cookie_domain:
        common_kwargs["domain"] = settings.auth_cookie_domain
    max_age = settings.refresh_token_expire_minutes * 60
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        **common_kwargs,
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        **common_kwargs,
    )


def _clear_refresh_cookies(response: Response) -> None:
    if not settings.auth_refresh_cookie_enabled:
        return
    cookie_kwargs = {
        "path": settings.auth_cookie_path,
        "samesite": settings.auth_cookie_samesite,
        "secure": settings.auth_cookie_secure,
    }
    if settings.auth_cookie_domain:
        cookie_kwargs["domain"] = settings.auth_cookie_domain
    response.delete_cookie(settings.auth_refresh_cookie_name, **cookie_kwargs)
    response.delete_cookie(settings.auth_csrf_cookie_name, **cookie_kwargs)


def _maybe_attach_cookies(
    response: Response,
    refresh_token: str | None,
) -> str | None:
    if not refresh_token or not settings.auth_refresh_cookie_enabled:
        return None
    csrf_token = _issue_csrf_token()
    _set_refresh_cookies(response, refresh_token, csrf_token)
    return csrf_token


def _issue_tokens(
    *,
    user: User,
    identity: Identity,
    org_id: str,
    mfa_authenticated: bool = False,
    mfa_method: str | None = None,
) -> tuple[str, str]:
    """Create access + refresh token pair for an org-scoped session."""
    access = create_access_token(
        str(user.id),
        org_id=org_id,
        identity_id=str(identity.id),
        is_superuser=user.is_superuser,
        token_version=identity.token_version,
        mfa_authenticated=mfa_authenticated,
        mfa_method=mfa_method,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=org_id,
        identity_id=str(identity.id),
        is_superuser=user.is_superuser,
        token_version=identity.token_version,
        mfa_authenticated=mfa_authenticated,
        mfa_method=mfa_method,
    )
    return access, refresh


async def _load_user_for_org(
    db: AsyncSession,
    *,
    identity_id,
    org_id: str,
    allow_pending: bool,
) -> User:
    """Find the org-scoped User for a given identity, check membership status."""
    stmt = (
        select(User)
        .options(selectinload(User.identity))
        .where(User.identity_id == identity_id, User.org_id == org_id)
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available")
    if not user.is_superuser:
        membership = await deps.get_membership(db, user_id=user.id, org_id=org_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available"
            )
        if not deps.membership_allows_auth(membership, allow_pending=allow_pending):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Membership is not active"
            )
    return user


# ─── New login flow ──────────────────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse, summary="Authenticate with email + password")
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """
    Step 1 of the login flow.  No X-Org-Id header required.

    Verifies identity-level credentials and returns a short-lived pre_org_token
    used to list orgs and select one.
    """
    client_ip = request.client.host if request.client else "unknown"
    await enforce_login_limits(client_ip, payload.email)

    stmt = select(Identity).where(Identity.email == payload.email)
    identity = (await db.execute(stmt)).scalar_one_or_none()

    if (
        not identity
        or not identity.is_active
        or not constant_time_verify(identity.hashed_password if identity else None, payload.password)
    ):
        await record_login_attempt(payload.email, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    await record_login_attempt(payload.email, success=True)

    pre_org_token = create_pre_org_token(str(identity.id))
    return LoginResponse(
        pre_org_token=pre_org_token,
        must_change_password=identity.must_change_password,
    )


@router.get("/orgs", response_model=AuthOrgsResponse, summary="List orgs for authenticated identity")
async def list_orgs(
    identity: Identity = Depends(deps.get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> AuthOrgsResponse:
    """
    Step 2 of the login flow.  Requires pre_org_token or access_token.

    Returns the list of orgs the identity has active memberships in.
    """
    stmt = (
        select(Org)
        .join(User, User.org_id == Org.id)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(
            User.identity_id == identity.id,
            User.is_active.is_(True),
            Org.status == "ACTIVE",
            OrgMembership.org_id == Org.id,
        )
        .order_by(Org.name.asc())
    )
    orgs = (await db.execute(stmt)).scalars().unique().all()
    summaries = [OrgSummary(org_id=org.id, name=org.name, slug=org.slug) for org in orgs]
    auto_selected = len(summaries) == 1
    return AuthOrgsResponse(orgs=summaries, auto_selected=auto_selected)


@router.post(
    "/select-org", response_model=SelectOrgResponse, summary="Select org and evaluate MFA policy"
)
async def select_org(
    payload: SelectOrgRequest,
    request: Request,
    response: Response,
    identity: Identity = Depends(deps.get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> SelectOrgResponse:
    """
    Step 3 of the login flow.  Requires pre_org_token.

    Evaluates the org's MFA policy and either:
    - Issues full access+refresh tokens (no MFA needed or device remembered)
    - Returns mfa_required=True with a challenge_token
    - Returns mfa_setup_required=True with a setup_token
    """
    user = await _load_user_for_org(
        db, identity_id=identity.id, org_id=payload.org_id, allow_pending=True
    )

    ctx = deps.TenantContext(org_id=payload.org_id)
    org_settings = await settings_service.get_org_settings(db, ctx)
    has_sensitive = await authz_service.has_sensitive_permissions(db, user, payload.org_id)

    mfa_required = bool(org_settings.require_two_factor or identity.mfa_enabled or has_sensitive)
    remember_days = org_settings.remember_device_days
    require_login_mfa = settings_service.is_mfa_action_required(org_settings, "LOGIN")

    # If org requires MFA (or user has sensitive perms) but identity hasn't set it up yet
    if (org_settings.require_two_factor or has_sensitive) and not identity.mfa_enabled:
        setup_token = create_mfa_setup_token(str(identity.id), payload.org_id)
        return SelectOrgResponse(
            mfa_setup_required=True,
            setup_token=setup_token,
            remember_device_days=remember_days,
        )

    now = datetime.now(timezone.utc)
    identity.last_active_at = now
    db.add(identity)
    await db.commit()

    if mfa_required:
        if require_login_mfa:
            # Force MFA at login regardless of remembered devices
            remember_device_token = None
        else:
            remember_device_token = payload.remember_device_token

        if remember_device_token and org_settings.remember_device_days > 0:
            device = await mfa_service.find_valid_device(
                db,
                org_id=payload.org_id,
                user_id=user.id,
                remember_token=remember_device_token,
            )
            if device:
                await db.commit()
                access, refresh = _issue_tokens(
                    user=user,
                    identity=identity,
                    org_id=payload.org_id,
                    mfa_authenticated=True,
                    mfa_method="remember_device",
                )
                result = SelectOrgResponse(
                    access_token=access,
                    refresh_token=refresh,
                    remember_device_days=remember_days,
                )
                csrf_token = _maybe_attach_cookies(response, result.refresh_token)
                if csrf_token:
                    result = result.model_copy(update={"csrf_token": csrf_token})
                return result

        challenge_token = create_mfa_challenge_token(str(identity.id), payload.org_id)
        return SelectOrgResponse(
            mfa_required=True,
            challenge_token=challenge_token,
            remember_device_days=remember_days,
        )

    # No MFA required — issue tokens directly
    access, refresh = _issue_tokens(
        user=user,
        identity=identity,
        org_id=payload.org_id,
        mfa_authenticated=False,
    )
    result = SelectOrgResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_days=remember_days,
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


@router.post(
    "/mfa/verify",
    response_model=MfaVerifyResponse,
    summary="Verify MFA during login (TOTP or recovery)",
)
async def mfa_verify(
    payload: MfaVerifyRequest,
    request: Request,
    response: Response,
    identity: Identity = Depends(deps.get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> MfaVerifyResponse:
    """
    Step 4a of the login flow. Verify TOTP or recovery code during login.
    Requires pre_org_token.
    """
    await enforce_mfa_rate_limit(payload.challenge_token)

    try:
        challenge = decode_mfa_challenge_token(payload.challenge_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired MFA token"
        ) from exc

    challenge_identity_id = challenge.get("sub")
    challenge_org_id = challenge.get("org")
    if str(identity.id) != challenge_identity_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Challenge does not match identity"
        )

    user = await _load_user_for_org(
        db, identity_id=identity.id, org_id=challenge_org_id, allow_pending=True
    )

    if not identity.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA not available")

    if payload.code_type == "recovery":
        is_valid = await mfa_service.verify_recovery_code(
            db, identity_id=identity.id, code=payload.code
        )
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recovery code"
            )
    else:
        if not identity.mfa_secret_encrypted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="MFA secret missing"
            )
        secret = mfa_service.decrypt_secret(identity.mfa_secret_encrypted)
        if not mfa_service.verify_totp(secret, payload.code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code"
            )

    now = datetime.now(timezone.utc)
    identity.last_active_at = now
    db.add(identity)
    await db.commit()

    remember_device_token = None
    if payload.remember_device:
        ctx = deps.TenantContext(org_id=challenge_org_id)
        org_settings = await settings_service.get_org_settings(db, ctx)
        days = org_settings.remember_device_days
        if days > 0:
            remember_device_token = await mfa_service.create_remember_device(
                db,
                org_id=challenge_org_id,
                user_id=user.id,
                expires_at=mfa_service.compute_device_expiry(days),
                user_agent=request.headers.get("user-agent"),
                ip_address=request.client.host if request.client else None,
            )

    await db.commit()
    access, refresh = _issue_tokens(
        user=user,
        identity=identity,
        org_id=challenge_org_id,
        mfa_authenticated=True,
        mfa_method=payload.code_type,
    )
    result = MfaVerifyResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


@router.post(
    "/mfa/enroll/start",
    response_model=MfaSetupStartResponse,
    summary="Start MFA enrollment during login",
)
async def mfa_enroll_start(
    payload: MfaEnrollStartRequest,
    identity: Identity = Depends(deps.get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> MfaSetupStartResponse:
    """
    Step 4b of the login flow. Generate TOTP secret for first-time MFA setup.
    Requires pre_org_token.
    """
    try:
        setup = decode_mfa_setup_token(payload.setup_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token"
        ) from exc

    setup_identity_id = setup.get("sub")
    setup_org_id = setup.get("org")
    if str(identity.id) != setup_identity_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Setup token does not match identity"
        )

    if identity.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA already enabled")

    secret = mfa_service.generate_totp_secret()
    identity.mfa_secret_encrypted = mfa_service.encrypt_secret(secret)
    identity.mfa_method = "TOTP"
    identity.mfa_enabled = False
    db.add(identity)
    await db.commit()

    org = (await db.execute(select(Org).where(Org.id == setup_org_id))).scalar_one_or_none()
    issuer = org.name if org else setup_org_id
    otpauth_url = mfa_service.build_totp_uri(secret, identity.email, issuer)
    ctx = deps.TenantContext(org_id=setup_org_id)
    org_settings = await settings_service.get_org_settings(db, ctx)
    return MfaSetupStartResponse(
        secret=secret,
        otpauth_url=otpauth_url,
        issuer=issuer,
        account=identity.email,
        remember_device_days=org_settings.remember_device_days,
    )


@router.post(
    "/mfa/enroll/verify",
    response_model=MfaSetupCompleteResponse,
    summary="Verify TOTP code to complete MFA enrollment during login",
)
async def mfa_enroll_verify(
    payload: MfaEnrollVerifyRequest,
    request: Request,
    response: Response,
    identity: Identity = Depends(deps.get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> MfaSetupCompleteResponse:
    """
    Step 4c of the login flow. Verify TOTP code, enable MFA, issue tokens.
    Requires pre_org_token.
    """
    await enforce_mfa_rate_limit(payload.setup_token)

    try:
        setup = decode_mfa_setup_token(payload.setup_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token"
        ) from exc

    setup_identity_id = setup.get("sub")
    setup_org_id = setup.get("org")
    if str(identity.id) != setup_identity_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Setup token does not match identity"
        )

    if not identity.mfa_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not initialized")

    secret = mfa_service.decrypt_secret(identity.mfa_secret_encrypted)
    if not mfa_service.verify_totp(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

    identity.mfa_enabled = True
    identity.mfa_method = "TOTP"
    identity.mfa_confirmed_at = datetime.now(timezone.utc)
    db.add(identity)
    await db.commit()

    user = await _load_user_for_org(
        db, identity_id=identity.id, org_id=setup_org_id, allow_pending=True
    )

    # Generate recovery codes
    recovery_codes = await mfa_service.generate_recovery_codes(db, identity_id=identity.id)

    remember_device_token = None
    if payload.remember_device:
        ctx = deps.TenantContext(org_id=setup_org_id)
        org_settings = await settings_service.get_org_settings(db, ctx)
        days = org_settings.remember_device_days
        if days > 0:
            remember_device_token = await mfa_service.create_remember_device(
                db,
                org_id=setup_org_id,
                user_id=user.id,
                expires_at=mfa_service.compute_device_expiry(days),
                user_agent=request.headers.get("user-agent"),
                ip_address=request.client.host if request.client else None,
            )

    await db.commit()
    access, refresh = _issue_tokens(
        user=user,
        identity=identity,
        org_id=setup_org_id,
        mfa_authenticated=True,
        mfa_method="totp",
    )
    result = MfaSetupCompleteResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
        recovery_codes=recovery_codes,
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


# ─── Post-login MFA management ──────────────────────────────────────────────


@router.post("/mfa/setup/start", response_model=MfaSetupStartResponse)
async def mfa_setup_start(
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MfaSetupStartResponse:
    identity = current_user.identity
    if identity.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA already enabled")

    secret = mfa_service.generate_totp_secret()
    identity.mfa_secret_encrypted = mfa_service.encrypt_secret(secret)
    identity.mfa_method = "TOTP"
    identity.mfa_enabled = False
    db.add(identity)
    await db.commit()

    org = (await db.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one_or_none()
    issuer = org.name if org else ctx.org_id
    otpauth_url = mfa_service.build_totp_uri(secret, identity.email, issuer)
    return MfaSetupStartResponse(
        secret=secret,
        otpauth_url=otpauth_url,
        issuer=issuer,
        account=identity.email,
    )


@router.post("/mfa/setup/verify", response_model=MfaSetupCompleteResponse)
async def mfa_setup_verify(
    payload: MfaSetupVerifyRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MfaSetupCompleteResponse:
    identity = current_user.identity
    if not identity.mfa_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not initialized")

    secret = mfa_service.decrypt_secret(identity.mfa_secret_encrypted)
    if not mfa_service.verify_totp(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

    identity.mfa_enabled = True
    identity.mfa_method = "TOTP"
    identity.mfa_confirmed_at = datetime.now(timezone.utc)
    db.add(identity)
    await db.commit()

    # Generate recovery codes
    recovery_codes = await mfa_service.generate_recovery_codes(db, identity_id=identity.id)

    remember_device_token = None
    if payload.remember_device:
        org_settings = await settings_service.get_org_settings(db, ctx)
        days = org_settings.remember_device_days
        if days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Remember device is disabled"
            )
        remember_device_token = await mfa_service.create_remember_device(
            db,
            org_id=ctx.org_id,
            user_id=current_user.id,
            expires_at=mfa_service.compute_device_expiry(days),
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )

    await db.commit()
    access, refresh = _issue_tokens(
        user=current_user,
        identity=identity,
        org_id=ctx.org_id,
        mfa_authenticated=True,
        mfa_method="totp",
    )
    result = MfaSetupCompleteResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
        recovery_codes=recovery_codes,
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


class RecoveryCodesCountResponse(BaseModel):
    remaining_count: int


@router.get("/mfa/recovery-codes/count", response_model=RecoveryCodesCountResponse)
async def get_recovery_codes_count(
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> RecoveryCodesCountResponse:
    """Get the number of remaining (unused) recovery codes."""
    identity = current_user.identity
    if not identity.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")

    count = await mfa_service.get_remaining_recovery_codes_count(db, identity_id=identity.id)
    return RecoveryCodesCountResponse(remaining_count=count)


class RegenerateRecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


@router.post("/mfa/recovery-codes/regenerate", response_model=RegenerateRecoveryCodesResponse)
async def regenerate_recovery_codes(
    request: Request,
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> RegenerateRecoveryCodesResponse:
    """
    Regenerate recovery codes. Requires step-up MFA verification.
    This invalidates all existing recovery codes.
    """
    identity = current_user.identity
    if not identity.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")

    # Require step-up MFA verification
    from app.api.auth_utils import require_step_up_mfa

    await require_step_up_mfa(request, current_user, ctx.org_id, action="RECOVERY_CODES_REGENERATE")

    recovery_codes = await mfa_service.generate_recovery_codes(db, identity_id=identity.id)
    await db.commit()
    return RegenerateRecoveryCodesResponse(recovery_codes=recovery_codes)


@router.post("/mfa/reset")
async def self_mfa_reset(
    request: Request,
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Self-service MFA reset. User must verify via step-up MFA.
    After reset, user will need to set up MFA again.
    """
    identity = current_user.identity
    if not identity.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")

    # Require step-up MFA verification
    from app.api.auth_utils import require_step_up_mfa

    await require_step_up_mfa(request, current_user, ctx.org_id, action="SELF_MFA_RESET")

    # Clear all MFA data on identity, delete per-org devices
    await mfa_service.clear_user_mfa(
        db, identity, org_id=ctx.org_id, user_id=current_user.id
    )
    await db.commit()

    return {"message": "MFA has been reset. Please set up MFA again."}


# ─── Session management ─────────────────────────────────────────────────────


@router.post("/refresh/csrf", response_model=CsrfTokenResponse)
async def refresh_csrf(
    request: Request,
    response: Response,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> CsrfTokenResponse:
    refresh_token = request.cookies.get(settings.auth_refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required"
        )
    try:
        token_data = decode_token(refresh_token, expected_type="refresh")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    token_org = token_data.get("org")
    if token_org and token_org != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch"
        )
    csrf_token = _issue_csrf_token()
    _set_refresh_cookies(response, refresh_token, csrf_token)
    return CsrfTokenResponse(csrf_token=csrf_token)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> TokenPair:
    refresh_token = payload.refresh_token if payload else None
    used_cookie = False
    if not refresh_token:
        refresh_token = request.cookies.get(settings.auth_refresh_cookie_name)
        used_cookie = bool(refresh_token)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Refresh token required"
        )
    if used_cookie and settings.auth_refresh_cookie_enabled:
        csrf_header = request.headers.get(settings.auth_csrf_header_name)
        csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
        if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed",
            )
    try:
        token_data = decode_token(refresh_token, expected_type="refresh")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user_id = token_data.get("sub")
    token_version = token_data.get("tv")
    jti = token_data.get("jti")
    exp_ts = token_data.get("exp")
    token_org = token_data.get("org")
    token_identity_id = token_data.get("iid")
    token_is_superuser = bool(token_data.get("su"))
    token_mfa = bool(token_data.get("mfa"))
    token_mfa_method = token_data.get("mfa_method")
    if not user_id or token_version is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not jti or not exp_ts:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not token_org:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing org claim"
        )
    if token_org and token_org != ctx.org_id and not token_is_superuser:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch"
        )

    # Load identity for token_version check
    identity = None
    if token_identity_id:
        identity = await db.get(Identity, token_identity_id)
    if not identity:
        # Fallback: load via user
        stmt = (
            select(User)
            .options(selectinload(User.identity))
            .where(User.id == user_id)
        )
        user = (await db.execute(stmt)).scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
            )
        identity = user.identity
    else:
        stmt = select(User).where(User.id == user_id)
        user = (await db.execute(stmt)).scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
            )

    if not identity or not identity.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identity inactive"
        )
    if identity.token_version != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    if not user.is_superuser:
        membership = await deps.get_membership(db, user_id=user.id, org_id=ctx.org_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not a member of org"
            )
        if not deps.membership_allows_auth(membership, allow_pending=True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Membership is not active"
            )

    now = datetime.now(timezone.utc)
    deps.enforce_inactivity(identity.last_active_at, now)
    identity.last_active_at = now
    db.add(identity)
    await db.commit()

    # Refresh token rotation: reject reused tokens (atomic check-and-mark)
    was_first_use = await mark_refresh_used_atomic(
        jti, datetime.fromtimestamp(exp_ts, tz=timezone.utc)
    )
    if not was_first_use:
        identity.token_version += 1
        db.add(identity)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token reuse detected"
        )

    access, refresh = _issue_tokens(
        user=user,
        identity=identity,
        org_id=ctx.org_id,
        mfa_authenticated=token_mfa,
        mfa_method=token_mfa_method,
    )
    result = TokenPair(access_token=access, refresh_token=refresh)
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    # Bump identity token_version — revokes ALL org sessions for this identity
    identity = current_user.identity
    identity.token_version += 1
    db.add(identity)
    await db.commit()
    _clear_refresh_cookies(response)
    return None


@router.get("/me", response_model=UserOut)
async def read_current_user(
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> UserOut:
    identity = current_user.identity
    return UserOut.model_validate(current_user).model_copy(
        update={
            "org_id": ctx.org_id,
            "mfa_enabled": identity.mfa_enabled if identity else False,
            "last_active_at": identity.last_active_at if identity else None,
        }
    )


@router.post("/change-password", response_model=TokenPair)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    current_user: User = Depends(deps.get_current_user_allow_password_change),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    identity = current_user.identity
    if not constant_time_verify(identity.hashed_password, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from current password",
        )

    try:
        identity.hashed_password = get_password_hash(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    was_first_login = identity.must_change_password
    identity.token_version += 1
    identity.last_active_at = now
    identity.must_change_password = False

    # If this is the first-time login flow, mark membership as accepted/active
    if was_first_login:
        membership_stmt = select(OrgMembership).where(
            OrgMembership.org_id == ctx.org_id,
            OrgMembership.user_id == current_user.id,
        )
        membership_result = await db.execute(membership_stmt)
        membership = membership_result.scalar_one_or_none()
        if membership:
            updated = False
            if membership.invitation_status != "ACCEPTED":
                membership.invitation_status = "ACCEPTED"
                membership.accepted_at = now
                updated = True
            if membership.platform_status != "ACTIVE":
                membership.platform_status = "ACTIVE"
                updated = True
            if updated:
                db.add(membership)

    db.add(identity)

    await db.commit()

    access, refresh = _issue_tokens(
        user=current_user,
        identity=identity,
        org_id=ctx.org_id,
        mfa_authenticated=identity.mfa_enabled,
    )
    result = TokenPair(access_token=access, refresh_token=refresh)
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


# ─── Step-up MFA ─────────────────────────────────────────────────────────────


@router.post(
    "/step-up/verify",
    response_model=StepUpVerifyResponse,
    summary="Verify step-up MFA for a sensitive action",
)
async def verify_step_up_mfa(
    payload: StepUpVerifyRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StepUpVerifyResponse:
    """
    Verify a TOTP code for step-up MFA.

    When a sensitive action requires step-up authentication, the endpoint
    returns a challenge token. Submit that token along with the TOTP code
    to this endpoint to receive a short-lived step-up token that authorizes
    the specific action.
    """
    # Rate limit step-up verification attempts
    await enforce_mfa_rate_limit(payload.challenge_token)

    try:
        challenge_data = decode_step_up_challenge_token(payload.challenge_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired challenge token",
        ) from exc

    # Verify the challenge belongs to the current user and org
    if challenge_data.get("sub") != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Challenge token does not match current user",
        )
    if challenge_data.get("org") != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Challenge token does not match current org",
        )

    action = challenge_data.get("action")
    if not action:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid challenge token: missing action",
        )

    # Verify the code against identity's MFA
    identity = current_user.identity
    if not identity.mfa_enabled or not identity.mfa_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this user",
        )

    if payload.code_type == "recovery":
        # Verify and consume recovery code
        is_valid = await mfa_service.verify_recovery_code(
            db, identity_id=identity.id, code=payload.code
        )
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid recovery code",
            )
    else:
        # Verify TOTP code
        secret = mfa_service.decrypt_secret(identity.mfa_secret_encrypted)
        if not mfa_service.verify_totp(secret, payload.code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )

    await db.commit()
    # Create step-up token valid for 5 minutes
    step_up_token = create_step_up_token(
        str(current_user.id),
        ctx.org_id,
        action,
        ttl_minutes=5,
    )

    return StepUpVerifyResponse(
        step_up_token=step_up_token,
        action=action,
        expires_in_seconds=300,  # 5 minutes
    )
