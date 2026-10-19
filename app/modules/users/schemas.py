import uuid
import re
from datetime import datetime, date
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

# =========================
# Enums
# =========================

from app.modules.auth.models import UserRole, ContactType

class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"

# =========================
# Sub-Schemas
# =========================

class UserProfileSchema(BaseModel):
    full_name: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[Gender] = None
    # mk_id profilda saqlanmaydi — u imtihon ro'yxatiga tegishli
    # (mock_registrations.student_id), qarang mock_test/mk_id.py.
    language: Optional[str] = "uz"
    timezone: Optional[str] = "Asia/Tashkent"
    
    model_config = ConfigDict(from_attributes=True)

class ContactSchema(BaseModel):
    id: int
    contact_type: ContactType
    value: str
    is_verified: bool
    is_primary: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# =========================
# Main Response Schemas
# =========================

class UserResponse(BaseModel):
    id: uuid.UUID
    is_active: bool
    force_password_change: bool
    global_role: UserRole
    created_at: datetime
    updated_at: datetime
    profile: Optional[UserProfileSchema] = None
    contacts: List[ContactSchema] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

# =========================
# Action Schemas (Missing ones added)
# =========================

class AvatarUpdateResponse(BaseModel):
    avatar_url: str

class AddContactSchema(BaseModel):
    contact_type: ContactType
    value: str

class SetPrimaryContactRequest(BaseModel):
    contact_id: int

class PhoneUpdateRequest(BaseModel):
    new_phone: str

    @field_validator("new_phone")
    @classmethod
    def validate_phone(cls, v):
        if not re.match(r"^\+998\d{9}$", v):
            raise ValueError("Telefon +998XXXXXXXXX formatida bo‘lishi kerak")
        return v

class PhoneVerifyRequest(BaseModel):
    new_phone: str
    verification_code: str = Field(..., min_length=4, max_length=6)

# =========================
# Session & Auth Schemas
# =========================

class UserSessionResponse(BaseModel):
    id: uuid.UUID
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    expires_at: datetime
    is_revoked: bool
    updated_at: datetime = Field(alias="last_active")

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=3, max_length=255)
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    bio: Optional[str] = None
    birth_date: Optional[date] = None
    language: Optional[str] = Field(None, max_length=10)
    timezone: Optional[str] = Field(None, max_length=50)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if v and not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("Username faqat harf, raqam va _ dan iborat bo‘lishi kerak")
        return v.lower() if v else v

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8)
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, info):
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Yangi parollar mos kelmadi")
        return v

class VerificationCodeResponse(BaseModel):
    target: str
    expires_at: datetime
    is_used: bool

    model_config = ConfigDict(from_attributes=True)