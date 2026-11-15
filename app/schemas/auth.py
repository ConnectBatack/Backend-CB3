from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    totp_code: str | None = Field(default=None, max_length=12)
    remember_device: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    must_change_password: bool
    totp_enabled: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    role: UserRole
    password: str = Field(min_length=12, max_length=200)
    must_change_password: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=200)
    must_change_password: bool = True


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class TotpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TotpVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str | None
    actor_email: str | None
    actor_role: str | None
    action: str
    resource_type: str
    resource_id: str | None
    ip_address: str | None
    details: dict[str, Any] | None
    created_at: datetime

