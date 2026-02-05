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


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginStartRequest(BaseModel):
    email: EmailStr


class LoginStartResponse(BaseModel):
    challenge_token: str


class LoginCompleteRequest(BaseModel):
    challenge_token: str
    password: str
    remember_device_token: str | None = None


class LoginCompleteResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    csrf_token: str | None = None
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: str | None = None
    setup_token: str | None = None
    remember_device_days: int | None = None


class LoginMfaRequest(BaseModel):
    mfa_token: str
    code: str
    remember_device: bool = False


class LoginMfaResponse(TokenPair):
    remember_device_token: str | None = None


class LoginMfaSetupStartRequest(BaseModel):
    setup_token: str


class LoginMfaSetupVerifyRequest(BaseModel):
    setup_token: str
    code: str
    remember_device: bool = False


class MfaSetupStartResponse(BaseModel):
    secret: str
    otpauth_url: str
    issuer: str
    account: str
    remember_device_days: int | None = None


class MfaSetupVerifyRequest(BaseModel):
    code: str
    remember_device: bool = False


class OrgDiscoveryRequest(BaseModel):
    email: EmailStr


class OrgSummary(BaseModel):
    org_id: str | None = None
    name: str
    slug: str | None = None


class OrgDiscoveryResponse(BaseModel):
    orgs: list[OrgSummary]


class OrgResolveResponse(BaseModel):
    org: OrgSummary


class UserOut(BaseModel):
    id: UUID
    org_id: str | None = None
    email: EmailStr
    is_active: bool
    is_superuser: bool
    mfa_enabled: bool
    last_active_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


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


# ─── MFA Recovery ─────────────────────────────────────────────────────────────


class MfaSetupCompleteResponse(BaseModel):
    """Response after completing MFA setup, includes recovery codes."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    csrf_token: str | None = None
    remember_device_token: str | None = None
    recovery_codes: list[str]


class LoginMfaRecoveryRequest(BaseModel):
    """Request to login using a recovery code instead of TOTP."""

    mfa_token: str
    recovery_code: str


class MfaResetRequest(BaseModel):
    """Request for self-service MFA reset (requires current TOTP or recovery code)."""

    code: str
    code_type: str = "totp"  # "totp" or "recovery"


class AdminMfaResetRequest(BaseModel):
    """Request for admin to reset a user's MFA."""

    reason: str | None = None
