from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    csrf_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ─── New login flow ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    pre_org_token: str
    must_change_password: bool = False


class OrgSummary(BaseModel):
    org_id: str | None = None
    name: str
    slug: str | None = None


class AuthOrgsResponse(BaseModel):
    orgs: list[OrgSummary]
    auto_selected: bool = False


class SelectOrgRequest(BaseModel):
    org_id: str
    remember_device_token: str | None = None


class SelectOrgResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    csrf_token: str | None = None
    mfa_required: bool = False
    mfa_setup_required: bool = False
    challenge_token: str | None = None
    setup_token: str | None = None
    remember_device_days: int | None = None


class MfaVerifyRequest(BaseModel):
    challenge_token: str
    code: str
    code_type: Literal["totp", "recovery"] = "totp"
    remember_device: bool = False


class MfaVerifyResponse(TokenPair):
    remember_device_token: str | None = None
    recovery_codes: list[str] | None = None


class MfaEnrollStartRequest(BaseModel):
    setup_token: str


class MfaEnrollVerifyRequest(BaseModel):
    setup_token: str
    code: str
    remember_device: bool = False


# ─── Post-login MFA setup/management ────────────────────────────────────────


class MfaSetupStartResponse(BaseModel):
    secret: str | None = None
    otpauth_url: str
    issuer: str
    account: str
    remember_device_days: int | None = None


class MfaSetupVerifyRequest(BaseModel):
    code: str
    remember_device: bool = False


class MfaSetupCompleteResponse(BaseModel):
    """Response after completing MFA setup, includes recovery codes."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    csrf_token: str | None = None
    remember_device_token: str | None = None
    recovery_codes: list[str]


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class UserOut(BaseModel):
    id: UUID
    org_id: str | None = None
    email: EmailStr
    is_active: bool
    is_superuser: bool
    mfa_enabled: bool = False
    full_name: str | None = None
    last_active_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ─── Step-up MFA ─────────────────────────────────────────────────────────────


class StepUpChallengeResponse(BaseModel):
    """Response when step-up MFA is required for a sensitive action."""

    step_up_required: bool = True
    challenge_token: str
    action: str


class StepUpVerifyRequest(BaseModel):
    """Request to verify step-up MFA."""

    challenge_token: str
    code: str
    code_type: Literal["totp", "recovery"] = "totp"


class StepUpVerifyResponse(BaseModel):
    """Response after successful step-up MFA verification."""

    step_up_token: str
    action: str
    expires_in_seconds: int


# ─── MFA Management ─────────────────────────────────────────────────────────


class MfaResetRequest(BaseModel):
    """Request for self-service MFA reset (requires current TOTP or recovery code)."""

    code: str
    code_type: str = "totp"  # "totp" or "recovery"


class AdminMfaResetRequest(BaseModel):
    """Request for admin to reset a user's MFA."""

    reason: str | None = None
