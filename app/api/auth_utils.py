from typing import Optional

from fastapi import Request

from app.core.security import verify_password, create_step_up_challenge_token, decode_step_up_token
from app.core.settings import settings
from app.utils.login_security import check_lockout, rate_limit, register_login_attempt
from app.models import User
from app.api.deps import StepUpMfaRequired

_FAKE_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWrn3ILAWO.P3K.fc8G2.0G7u6g.2"


def _extract_step_up_token(request: Request) -> str | None:
    """Extract step-up token from X-Step-Up-Token header."""
    return request.headers.get("X-Step-Up-Token")


async def require_step_up_mfa(
    request: Request,
    current_user: User,
    org_id: str,
    action: str = "RECOVERY_CODES_REGENERATE",
) -> None:
    """
    Require step-up MFA verification for a sensitive action.
    Raises StepUpMfaRequired if no valid step-up token is present.
    """
    step_up_token = _extract_step_up_token(request)
    if step_up_token:
        try:
            step_up_payload = decode_step_up_token(step_up_token)
            # Verify the step-up token matches this user, org, and action
            if (
                step_up_payload.get("sub") == str(current_user.id)
                and step_up_payload.get("org") == org_id
                and step_up_payload.get("action") == action
            ):
                return  # Step-up MFA already completed
        except ValueError:
            pass  # Invalid step-up token, continue to require new challenge

    # No valid step-up token, create a challenge and raise exception
    challenge_token = create_step_up_challenge_token(
        str(current_user.id),
        org_id,
        action,
    )
    raise StepUpMfaRequired(challenge_token=challenge_token, action=action)


def constant_time_verify(user_password_hash: Optional[str], password: str) -> bool:
    if user_password_hash:
        return verify_password(password, user_password_hash)
    # Dummy verification to equalize timing
    verify_password(password, _FAKE_HASH)
    return False


async def enforce_login_limits(ip: str, email: str) -> None:
    await rate_limit(f"ip:{ip}", limit=settings.rate_limit_per_minute, window_seconds=60)
    await rate_limit(f"email:{email}", limit=settings.rate_limit_per_minute, window_seconds=60)
    await check_lockout(email)


async def record_login_attempt(email: str, success: bool) -> None:
    await register_login_attempt(email, success)
