from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    org_id: str
    email: EmailStr
    is_active: bool
    is_superuser: bool
    mfa_enabled: bool
    last_active_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
