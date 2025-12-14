from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    create_login_challenge_token,
    create_refresh_token,
    decode_login_challenge_token,
    decode_token,
    get_password_hash,
)
from app.core.settings import settings
from app.db.session import get_db
from app.models import OrgMembership, User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginCompleteRequest,
    LoginRequest,
    LoginStartRequest,
    LoginStartResponse,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.api.auth_utils import constant_time_verify, enforce_login_limits, record_login_attempt
from app.utils.login_security import is_refresh_used, mark_refresh_used

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
@limiter.limit(lambda: f"{settings.rate_limit_per_minute}/minute")
async def login(
    credentials: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> TokenPair:
    """Single-call login (legacy). Equivalent to start + complete in one request."""
    client_ip = request.client.host if request.client else "unknown"
    await enforce_login_limits(client_ip, credentials.email)
    challenge = create_login_challenge_token(credentials.email, ctx.org_id)
    return await _complete_login_flow(
        challenge_token=challenge,
        password=credentials.password,
        ctx=ctx,
        db=db,
        client_ip=client_ip,
    )


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
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or inactive user")

    challenge = create_login_challenge_token(payload.email, ctx.org_id)
    return LoginStartResponse(challenge_token=challenge)


@router.post("/login/complete", response_model=TokenPair)
async def login_complete(
    payload: LoginCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
) -> TokenPair:
    client_ip = request.client.host if request.client else "unknown"
    return await _complete_login_flow(
        challenge_token=payload.challenge_token,
        password=payload.password,
        ctx=ctx,
        db=db,
        client_ip=client_ip,
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
    if not user_id or token_version is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not jti or not exp_ts:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

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

    access = create_access_token(str(user.id), token_version=user.token_version)
    refresh = create_refresh_token(str(user.id), token_version=user.token_version)
    return TokenPair(access_token=access, refresh_token=refresh)


async def _complete_login_flow(
    *,
    challenge_token: str,
    password: str,
    ctx: deps.TenantContext,
    db: AsyncSession,
    client_ip: str,
) -> TokenPair:
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
    if not user or not constant_time_verify(user.hashed_password if user else None, password):
        await record_login_attempt(email, success=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    now = datetime.now(timezone.utc)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await record_login_attempt(email, success=True)

    access = create_access_token(str(user.id), token_version=user.token_version)
    refresh = create_refresh_token(str(user.id), token_version=user.token_version)
    return TokenPair(access_token=access, refresh_token=refresh)


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

    current_user.token_version += 1
    current_user.last_active_at = datetime.now(timezone.utc)
    current_user.must_change_password = False
    db.add(current_user)

    await db.commit()
    await db.refresh(current_user)

    access = create_access_token(str(current_user.id), token_version=current_user.token_version)
    refresh = create_refresh_token(str(current_user.id), token_version=current_user.token_version)
    return TokenPair(access_token=access, refresh_token=refresh)
