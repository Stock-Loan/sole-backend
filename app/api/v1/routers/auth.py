from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.limiter import limiter
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.core.settings import settings
from app.db.session import get_db
from app.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserOut
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
    client_ip = request.client.host if request.client else "unknown"
    await enforce_login_limits(client_ip, credentials.email)

    stmt = select(User).where(User.org_id == ctx.org_id, User.email == credentials.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or not constant_time_verify(user.hashed_password if user else None, credentials.password):
        await record_login_attempt(credentials.email, success=False)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    now = datetime.now(timezone.utc)
    user.last_active_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await record_login_attempt(credentials.email, success=True)

    access = create_access_token(str(user.id), token_version=user.token_version)
    refresh = create_refresh_token(str(user.id), token_version=user.token_version)
    return TokenPair(access_token=access, refresh_token=refresh)


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
