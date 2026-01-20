from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.security import (
    create_access_token,
    create_login_challenge_token,
    create_mfa_challenge_token,
    create_mfa_setup_token,
    create_refresh_token,
    decode_login_challenge_token,
    decode_mfa_challenge_token,
    decode_mfa_setup_token,
    decode_token,
    get_password_hash,
)
from app.db.session import get_db
from app.models import OrgMembership, User
from app.models.org import Org
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginCompleteRequest,
    LoginCompleteResponse,
    LoginMfaRequest,
    LoginMfaResponse,
    LoginMfaSetupStartRequest,
    LoginMfaSetupVerifyRequest,
    LoginStartRequest,
    LoginStartResponse,
    MfaSetupStartResponse,
    MfaSetupVerifyRequest,
    OrgDiscoveryRequest,
    OrgDiscoveryResponse,
    OrgResolveResponse,
    OrgSummary,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.api.auth_utils import constant_time_verify, enforce_login_limits, record_login_attempt
from app.services import mfa as mfa_service, settings as settings_service
from app.utils.login_security import is_refresh_used, mark_refresh_used

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/org-discovery", response_model=OrgDiscoveryResponse, summary="Discover org(s) by email")
async def discover_orgs_by_email(
    payload: OrgDiscoveryRequest,
    db: AsyncSession = Depends(get_db),
) -> OrgDiscoveryResponse:
    stmt = (
        select(Org)
        .join(User, User.org_id == Org.id)
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="org_id or slug is required")
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
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginCompleteResponse:
    return await _complete_login_flow(
        challenge_token=payload.challenge_token,
        password=payload.password,
        remember_device_token=payload.remember_device_token,
        ctx=ctx,
        db=db,
    )


@router.post("/login/mfa", response_model=LoginMfaResponse)
async def login_mfa(
    payload: LoginMfaRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginMfaResponse:
    try:
        challenge = decode_mfa_challenge_token(payload.mfa_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired MFA token") from exc

    if challenge.get("org") != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA token does not match tenant")

    user_id = challenge.get("sub")
    stmt = select(User).where(User.id == user_id, User.org_id == ctx.org_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not user.is_active or not user.mfa_enabled:
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Remember device is disabled")
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
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
    )
    return LoginMfaResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
    )


@router.post("/login/mfa/setup/start", response_model=MfaSetupStartResponse)
async def login_mfa_setup_start(
    payload: LoginMfaSetupStartRequest,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> MfaSetupStartResponse:
    try:
        setup = decode_mfa_setup_token(payload.setup_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token") from exc

    if setup.get("org") != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup token does not match tenant")

    user_id = setup.get("sub")
    stmt = select(User).where(User.id == user_id, User.org_id == ctx.org_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available")
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
    return MfaSetupStartResponse(secret=secret, otpauth_url=otpauth_url, issuer=issuer, account=user.email)


@router.post("/login/mfa/setup/verify", response_model=LoginMfaResponse)
async def login_mfa_setup_verify(
    payload: LoginMfaSetupVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> LoginMfaResponse:
    try:
        setup = decode_mfa_setup_token(payload.setup_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired setup token") from exc

    if setup.get("org") != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup token does not match tenant")

    user_id = setup.get("sub")
    stmt = select(User).where(User.id == user_id, User.org_id == ctx.org_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available")
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

    remember_device_token = None
    if payload.remember_device:
        org_settings = await settings_service.get_org_settings(db, ctx)
        days = org_settings.remember_device_days
        if days <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Remember device is disabled")
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
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=True,
    )
    return LoginMfaResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
    )


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


@router.post("/mfa/setup/verify", response_model=LoginMfaResponse)
async def mfa_setup_verify(
    payload: MfaSetupVerifyRequest,
    request: Request,
    current_user: User = Depends(deps.get_current_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoginMfaResponse:
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

    remember_device_token = None
    if payload.remember_device:
        org_settings = await settings_service.get_org_settings(db, ctx)
        days = org_settings.remember_device_days
        if days <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Remember device is disabled")
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
        org_id=current_user.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=True,
    )
    refresh = create_refresh_token(
        str(current_user.id),
        org_id=current_user.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=True,
    )
    return LoginMfaResponse(
        access_token=access,
        refresh_token=refresh,
        remember_device_token=remember_device_token,
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> TokenPair:
    token_data = decode_token(payload.refresh_token, expected_type="refresh")
    user_id = token_data.get("sub")
    token_version = token_data.get("tv")
    jti = token_data.get("jti")
    exp_ts = token_data.get("exp")
    token_org = token_data.get("org")
    token_is_superuser = bool(token_data.get("su"))
    token_mfa = bool(token_data.get("mfa"))
    if not user_id or token_version is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not jti or not exp_ts:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if token_org and token_org != ctx.org_id and not token_is_superuser:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tenant mismatch")

    stmt = select(User).where(User.id == user_id, User.org_id == ctx.org_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.token_version != token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token reuse detected")
    await mark_refresh_used(jti, datetime.fromtimestamp(exp_ts, tz=timezone.utc))

    access = create_access_token(
        str(user.id),
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=token_mfa,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=token_mfa,
    )
    return TokenPair(access_token=access, refresh_token=refresh)


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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge") from exc

    if challenge.get("org") != ctx.org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge does not match tenant")

    email = challenge.get("sub")
    stmt = select(User).where(User.org_id == ctx.org_id, User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not constant_time_verify(user.hashed_password if user else None, password):
        await record_login_attempt(email, success=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    org_settings = await settings_service.get_org_settings(db, ctx)
    mfa_required = bool(org_settings.require_two_factor or user.mfa_enabled)
    if mfa_required and not user.mfa_enabled:
        setup_token = create_mfa_setup_token(str(user.id), ctx.org_id)
        await record_login_attempt(email, success=True)
        return LoginCompleteResponse(mfa_setup_required=True, setup_token=setup_token)

    now = datetime.now(timezone.utc)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await record_login_attempt(email, success=True)

    if mfa_required:
        if remember_device_token:
            device = await mfa_service.find_valid_device(
                db,
                org_id=ctx.org_id,
                user_id=user.id,
                remember_token=remember_device_token,
            )
            if device:
                access = create_access_token(
                    str(user.id),
                    org_id=user.org_id,
                    is_superuser=user.is_superuser,
                    token_version=user.token_version,
                    mfa_authenticated=True,
                )
                refresh = create_refresh_token(
                    str(user.id),
                    org_id=user.org_id,
                    is_superuser=user.is_superuser,
                    token_version=user.token_version,
                    mfa_authenticated=True,
                )
                return LoginCompleteResponse(access_token=access, refresh_token=refresh)

        mfa_token = create_mfa_challenge_token(str(user.id), ctx.org_id)
        return LoginCompleteResponse(mfa_required=True, mfa_token=mfa_token)

    access = create_access_token(
        str(user.id),
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=False,
    )
    refresh = create_refresh_token(
        str(user.id),
        org_id=user.org_id,
        is_superuser=user.is_superuser,
        token_version=user.token_version,
        mfa_authenticated=False,
    )
    return LoginCompleteResponse(access_token=access, refresh_token=refresh)


@router.post("/logout", status_code=204)
async def logout(
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    current_user.token_version += 1
    db.add(current_user)
    await db.commit()
    return None


@router.get("/me", response_model=UserOut)
async def read_current_user(current_user: User = Depends(deps.get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/change-password", response_model=TokenPair)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(deps.get_current_user_allow_password_change),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    if not constant_time_verify(current_user.hashed_password, payload.current_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from current password")

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
        org_id=current_user.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=current_user.mfa_enabled,
    )
    refresh = create_refresh_token(
        str(current_user.id),
        org_id=current_user.org_id,
        is_superuser=current_user.is_superuser,
        token_version=current_user.token_version,
        mfa_authenticated=current_user.mfa_enabled,
    )
    return TokenPair(access_token=access, refresh_token=refresh)
