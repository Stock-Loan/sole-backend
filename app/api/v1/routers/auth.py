from datetime import datetime, timezone

import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.security import (
    create_access_token,
    create_login_challenge_token,
    create_mfa_challenge_token,
    create_mfa_setup_token,
    create_refresh_token,
    create_step_up_token,
    decode_login_challenge_token,
    decode_mfa_challenge_token,
    decode_mfa_setup_token,
    decode_step_up_challenge_token,
    decode_token,
    get_password_hash,
)
from app.core.settings import settings
from app.db.session import get_db
from app.models import OrgMembership, User
from app.models.org import Org
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginCompleteRequest,
    LoginCompleteResponse,
    LoginMfaRequest,
    LoginMfaResponse,
    LoginMfaRecoveryRequest,
    LoginMfaSetupStartRequest,
    LoginMfaSetupVerifyRequest,
    LoginStartRequest,
    LoginStartResponse,
    MfaSetupCompleteResponse,
    MfaSetupStartResponse,
    MfaSetupVerifyRequest,
    OrgDiscoveryRequest,
    OrgDiscoveryResponse,
    OrgResolveResponse,
    OrgSummary,
    CsrfTokenResponse,
    RefreshRequest,
    StepUpVerifyRequest,
    StepUpVerifyResponse,
    TokenPair,
    UserOut,
)
from app.api.auth_utils import constant_time_verify, enforce_login_limits, record_login_attempt
from app.services import authz as authz_service, mfa as mfa_service, settings as settings_service
from app.utils.login_security import enforce_mfa_rate_limit, is_refresh_used, mark_refresh_used

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _set_refresh_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    if not settings.auth_refresh_cookie_enabled:
        return
    cookie_kwargs = {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": settings.auth_cookie_samesite,
        "path": settings.auth_cookie_path,
    }
    if settings.auth_cookie_domain:
        cookie_kwargs["domain"] = settings.auth_cookie_domain
    max_age = settings.refresh_token_expire_minutes * 60
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        **cookie_kwargs,
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        **cookie_kwargs,
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


async def _load_user_for_org(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: deps.TenantContext,
    allow_pending: bool,
) -> User:
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available")
    if not user.is_superuser:
        membership = await deps.get_membership(db, user_id=user.id, org_id=ctx.org_id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available"
            )
        if not deps.membership_allows_auth(membership, allow_pending=allow_pending):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Membership is not active"
            )
    return user


@router.post(
    "/org-discovery", response_model=OrgDiscoveryResponse, summary="Discover org(s) by email"
)
async def discover_orgs_by_email(
    payload: OrgDiscoveryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OrgDiscoveryResponse:
    # Rate limit org discovery to prevent enumeration attacks
    client_ip = request.client.host if request.client else "unknown"
    await enforce_login_limits(client_ip, payload.email)

    stmt = (
        select(Org)
        .join(OrgMembership, OrgMembership.org_id == Org.id)
        .join(User, User.id == OrgMembership.user_id)
        .where(User.email == payload.email, Org.status == "ACTIVE")
        .order_by(Org.name.asc())
    )
    orgs = (await db.execute(stmt)).scalars().all()
    summaries = [OrgSummary(org_id=org.id, name=org.name, slug=org.slug) for org in orgs]
    return OrgDiscoveryResponse(orgs=summaries)


@router.get("/orgs/resolve", response_model=OrgResolveResponse, summary="Resolve org by id or slug")
async def resolve_org(
    org_id: str | None = None,
    slug: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> OrgResolveResponse:
    if not org_id and not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="org_id or slug is required"
        )
    stmt = select(Org).where(Org.status == "ACTIVE")
    if org_id:
        stmt = stmt.where(Org.id == org_id)
    if slug:
        stmt = stmt.where(Org.slug == slug)
    org = (await db.execute(stmt)).scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    summary = OrgSummary(org_id=org.id, name=org.name, slug=org.slug)
    return OrgResolveResponse(org=summary)


@router.post("/login/start", response_model=LoginStartResponse)
async def login_start(
    payload: LoginStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginStartResponse:
    client_ip = request.client.host if request.client else "unknown"
    await enforce_login_limits(client_ip, payload.email)

    stmt = select(User).where(User.org_id == ctx.org_id, User.email == payload.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Always return a challenge to avoid user enumeration; inactive or missing users will fail at completion.
    target_email = payload.email if user else payload.email
    challenge = create_login_challenge_token(target_email, ctx.org_id)
    return LoginStartResponse(challenge_token=challenge)


@router.post("/login/complete", response_model=LoginCompleteResponse)
async def login_complete(
    payload: LoginCompleteRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginCompleteResponse:
    result = await _complete_login_flow(
        challenge_token=payload.challenge_token,
        password=payload.password,
        remember_device_token=payload.remember_device_token,
        ctx=ctx,
        db=db,
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


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


@router.post("/login/mfa", response_model=LoginMfaResponse)
async def login_mfa(
    payload: LoginMfaRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginMfaResponse:
    # Rate limit MFA attempts to prevent brute-forcing the 6-digit code
    await enforce_mfa_rate_limit(payload.mfa_token)

    try:
        challenge = decode_mfa_challenge_token(payload.mfa_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired MFA token"
        ) from exc

    if challenge.get("org") != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="MFA token does not match tenant"
        )

    user_id = challenge.get("sub")
    user = await _load_user_for_org(db, user_id=user_id, ctx=ctx, allow_pending=True)
    if not user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA not available")
    if not user.mfa_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA secret missing")

    secret = mfa_service.decrypt_secret(user.mfa_secret_encrypted)
    if not mfa_service.verify_totp(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

    now = datetime.now(timezone.utc)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

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
            user_id=user.id,
            expires_at=mfa_service.compute_device_expiry(days),
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )

    access = create_access_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
        mfa_method="totp",
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
        mfa_method="totp",
    )
    result = LoginMfaResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


@router.post("/login/mfa/recovery", response_model=LoginMfaResponse)
async def login_mfa_recovery(
    payload: LoginMfaRecoveryRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginMfaResponse:
    """
    Complete MFA login using a recovery code instead of TOTP.
    Recovery codes are one-time use.
    """
    # Rate limit recovery code attempts
    await enforce_mfa_rate_limit(payload.mfa_token)

    try:
        challenge = decode_mfa_challenge_token(payload.mfa_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired MFA token"
        ) from exc

    if challenge.get("org") != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="MFA token does not match tenant"
        )

    user_id = challenge.get("sub")
    user = await _load_user_for_org(db, user_id=user_id, ctx=ctx, allow_pending=True)
    if not user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MFA not available")

    # Verify recovery code (marks it as used if valid)
    is_valid = await mfa_service.verify_recovery_code(
        db, org_id=ctx.org_id, user_id=user.id, code=payload.recovery_code
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recovery code"
        )

    now = datetime.now(timezone.utc)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access = create_access_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
        mfa_method="recovery",
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
        mfa_method="recovery",
    )
    result = LoginMfaResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=None,  # No remember device for recovery code login
    )
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


@router.post("/login/mfa/setup/start", response_model=MfaSetupStartResponse)
async def login_mfa_setup_start(
    payload: LoginMfaSetupStartRequest,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> MfaSetupStartResponse:
    try:
        setup = decode_mfa_setup_token(payload.setup_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token"
        ) from exc

    if setup.get("org") != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Setup token does not match tenant"
        )

    user_id = setup.get("sub")
    user = await _load_user_for_org(db, user_id=user_id, ctx=ctx, allow_pending=True)
    if user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA already enabled")

    secret = mfa_service.generate_totp_secret()
    user.mfa_secret_encrypted = mfa_service.encrypt_secret(secret)
    user.mfa_method = "TOTP"
    user.mfa_enabled = False
    db.add(user)
    await db.commit()
    await db.refresh(user)

    org = (await db.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one_or_none()
    issuer = org.name if org else ctx.org_id
    otpauth_url = mfa_service.build_totp_uri(secret, user.email, issuer)
    org_settings = await settings_service.get_org_settings(db, ctx)
    return MfaSetupStartResponse(
        secret=secret,
        otpauth_url=otpauth_url,
        issuer=issuer,
        account=user.email,
        remember_device_days=org_settings.remember_device_days,
    )


@router.post("/login/mfa/setup/verify", response_model=MfaSetupCompleteResponse)
async def login_mfa_setup_verify(
    payload: LoginMfaSetupVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> MfaSetupCompleteResponse:
    # Rate limit MFA setup verification attempts
    await enforce_mfa_rate_limit(payload.setup_token)

    try:
        setup = decode_mfa_setup_token(payload.setup_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token"
        ) from exc

    if setup.get("org") != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Setup token does not match tenant"
        )

    user_id = setup.get("sub")
    user = await _load_user_for_org(db, user_id=user_id, ctx=ctx, allow_pending=True)
    if not user.mfa_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not initialized")

    secret = mfa_service.decrypt_secret(user.mfa_secret_encrypted)
    if not mfa_service.verify_totp(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

    user.mfa_enabled = True
    user.mfa_method = "TOTP"
    user.mfa_confirmed_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Generate recovery codes
    recovery_codes = await mfa_service.generate_recovery_codes(
        db, org_id=ctx.org_id, user_id=user.id
    )

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
            user_id=user.id,
            expires_at=mfa_service.compute_device_expiry(days),
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )

    access = create_access_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
        mfa_method="totp",
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
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


@router.post("/mfa/setup/start", response_model=MfaSetupStartResponse)
async def mfa_setup_start(
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MfaSetupStartResponse:
    if current_user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA already enabled")

    secret = mfa_service.generate_totp_secret()
    current_user.mfa_secret_encrypted = mfa_service.encrypt_secret(secret)
    current_user.mfa_method = "TOTP"
    current_user.mfa_enabled = False
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    org = (await db.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one_or_none()
    issuer = org.name if org else ctx.org_id
    otpauth_url = mfa_service.build_totp_uri(secret, current_user.email, issuer)
    return MfaSetupStartResponse(
        secret=secret,
        otpauth_url=otpauth_url,
        issuer=issuer,
        account=current_user.email,
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
    if not current_user.mfa_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not initialized")

    secret = mfa_service.decrypt_secret(current_user.mfa_secret_encrypted)
    if not mfa_service.verify_totp(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

    current_user.mfa_enabled = True
    current_user.mfa_method = "TOTP"
    current_user.mfa_confirmed_at = datetime.now(timezone.utc)
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    # Generate recovery codes
    recovery_codes = await mfa_service.generate_recovery_codes(
        db, org_id=ctx.org_id, user_id=current_user.id
    )

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

    access = create_access_token(
        str(current_user.id),
        org_id=ctx.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=True,
        mfa_method="totp",
    )
    refresh = create_refresh_token(
        str(current_user.id),
        org_id=ctx.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
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
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")

    count = await mfa_service.get_remaining_recovery_codes_count(
        db, org_id=ctx.org_id, user_id=current_user.id
    )
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
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")

    # Require step-up MFA verification
    from app.api.auth_utils import require_step_up_mfa

    await require_step_up_mfa(request, current_user, ctx.org_id, action="RECOVERY_CODES_REGENERATE")

    recovery_codes = await mfa_service.generate_recovery_codes(
        db, org_id=ctx.org_id, user_id=current_user.id
    )
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
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not enabled")

    # Require step-up MFA verification
    from app.api.auth_utils import require_step_up_mfa

    await require_step_up_mfa(request, current_user, ctx.org_id, action="SELF_MFA_RESET")

    # Clear all MFA data
    await mfa_service.clear_user_mfa(db, org_id=ctx.org_id, user=current_user)

    return {"message": "MFA has been reset. Please set up MFA again."}


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

    user = await _load_user_for_org(db, user_id=user_id, ctx=ctx, allow_pending=True)
    if user.token_version != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    now = datetime.now(timezone.utc)
    deps.enforce_inactivity(user.last_active_at, now)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Refresh token rotation: reject reused tokens
    if await is_refresh_used(jti):
        user.token_version += 1
        db.add(user)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token reuse detected"
        )
    await mark_refresh_used(jti, datetime.fromtimestamp(exp_ts, tz=timezone.utc))

    access = create_access_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=token_mfa,
        mfa_method=token_mfa_method,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=token_mfa,
        mfa_method=token_mfa_method,
    )
    result = TokenPair(access_token=access, refresh_token=refresh)
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


async def _complete_login_flow(
    *,
    challenge_token: str,
    password: str,
    remember_device_token: str | None,
    ctx: deps.TenantContext,
    db: AsyncSession,
) -> LoginCompleteResponse:
    try:
        challenge = decode_login_challenge_token(challenge_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge"
        ) from exc

    if challenge.get("org") != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge does not match tenant"
        )

    email = challenge.get("sub")
    stmt = select(User).where(User.org_id == ctx.org_id, User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if (
        not user
        or not user.is_active
        or not constant_time_verify(user.hashed_password if user else None, password)
    ):
        await record_login_attempt(email, success=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    membership = None
    if not user.is_superuser:
        membership = await deps.get_membership(db, user_id=user.id, org_id=ctx.org_id)
        if not membership:
            await record_login_attempt(email, success=False)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )
        if not deps.membership_allows_auth(membership, allow_pending=True):
            await record_login_attempt(email, success=False)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )

    org_settings = await settings_service.get_org_settings(db, ctx)

    # Check for sensitive permissions which mandate MFA
    has_sensitive = await authz_service.has_sensitive_permissions(db, user, ctx.org_id)

    # MFA is required if org mandates it, OR user has enabled it, OR user has sensitive permissions
    mfa_required = bool(org_settings.require_two_factor or user.mfa_enabled or has_sensitive)
    remember_days = org_settings.remember_device_days
    require_login_mfa = settings_service.is_mfa_action_required(org_settings, "LOGIN")

    # If org requires MFA or user has sensitive permissions, but user hasn't set it up yet, prompt for setup
    if (org_settings.require_two_factor or has_sensitive) and not user.mfa_enabled:
        setup_token = create_mfa_setup_token(str(user.id), ctx.org_id)
        await record_login_attempt(email, success=True)
        return LoginCompleteResponse(
            mfa_setup_required=True,
            setup_token=setup_token,
            remember_device_days=remember_days,
        )

    now = datetime.now(timezone.utc)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await record_login_attempt(email, success=True)

    if mfa_required:
        if require_login_mfa:
            remember_device_token = None
        if remember_device_token and org_settings.remember_device_days > 0:
            device = await mfa_service.find_valid_device(
                db,
                org_id=ctx.org_id,
                user_id=user.id,
                remember_token=remember_device_token,
            )
            if device:
                access = create_access_token(
                    str(user.id),
                    org_id=ctx.org_id,
                    is_superuser=user.is_superuser,
                    token_version=user.token_version,
                    mfa_authenticated=True,
                    mfa_method="remember_device",
                )
                refresh = create_refresh_token(
                    str(user.id),
                    org_id=ctx.org_id,
                    is_superuser=user.is_superuser,
                    token_version=user.token_version,
                    mfa_authenticated=True,
                    mfa_method="remember_device",
                )
                return LoginCompleteResponse(
                    access_token=access,
                    refresh_token=refresh,
                    remember_device_days=remember_days,
                )

        mfa_token = create_mfa_challenge_token(str(user.id), ctx.org_id)
        return LoginCompleteResponse(
            mfa_required=True,
            mfa_token=mfa_token,
            remember_device_days=remember_days,
        )

    access = create_access_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=False,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=ctx.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=False,
    )
    return LoginCompleteResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_days=remember_days,
    )


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    current_user.token_version += 1
    db.add(current_user)
    await db.commit()
    _clear_refresh_cookies(response)
    return None


@router.get("/me", response_model=UserOut)
async def read_current_user(
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> UserOut:
    return UserOut.model_validate(current_user).model_copy(update={"org_id": ctx.org_id})


@router.post("/change-password", response_model=TokenPair)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    current_user: User = Depends(deps.get_current_user_allow_password_change),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    if not constant_time_verify(current_user.hashed_password, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from current password",
        )

    try:
        current_user.hashed_password = get_password_hash(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    was_first_login = current_user.must_change_password
    current_user.token_version += 1
    current_user.last_active_at = now
    current_user.must_change_password = False

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

    db.add(current_user)

    await db.commit()
    await db.refresh(current_user)

    access = create_access_token(
        str(current_user.id),
        org_id=ctx.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=current_user.mfa_enabled,
    )
    refresh = create_refresh_token(
        str(current_user.id),
        org_id=ctx.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=current_user.mfa_enabled,
    )
    result = TokenPair(access_token=access, refresh_token=refresh)
    csrf_token = _maybe_attach_cookies(response, result.refresh_token)
    if csrf_token:
        result = result.model_copy(update={"csrf_token": csrf_token})
    return result


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

    # Verify the code (TOTP or recovery code)
    if not current_user.mfa_enabled or not current_user.mfa_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this user",
        )

    if payload.code_type == "recovery":
        # Verify and consume recovery code
        is_valid = await mfa_service.verify_recovery_code(
            db, org_id=ctx.org_id, user_id=current_user.id, code=payload.code
        )
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid recovery code",
            )
    else:
        # Verify TOTP code
        secret = mfa_service.decrypt_secret(current_user.mfa_secret_encrypted)
        if not mfa_service.verify_totp(secret, payload.code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )

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
