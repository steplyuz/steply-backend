from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from app.modules.auth.phone import normalize_uz_phone

PASSWORD_MIN = 8
PASSWORD_MAX = 128
OTP_CODE_PATTERN = r"^\d{6}$"


def _phone_validator(v: str) -> str:
    return normalize_uz_phone(v)

class Token(BaseModel):
    access_token: str
    refresh_token: str

class StatusResponse(BaseModel):
    status: str
    message: Optional[str] = None
    bot_link: Optional[str] = None

class TelegramVerifyRequest(BaseModel):
    """id_token returned by telegram-login.js (popup flow)."""
    id_token: str = Field(min_length=20, max_length=8192)

class GoogleVerifyRequest(BaseModel):
    """`credential` (id_token) — Google Identity Services callback'idan."""
    id_token: str = Field(min_length=20, max_length=8192)

class PhoneLoginRequest(BaseModel):
    phone: str
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)

    _normalize_phone = field_validator("phone")(_phone_validator)

class PhoneAuthResponse(BaseModel):
    status: str
    token: Optional[Token] = None
    force_password_change: Optional[bool] = None
    message: Optional[str] = None

class RegisterRequest(BaseModel):
    phone: str
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    full_name: str = Field(min_length=2, max_length=120)
    birth_date: str = Field(min_length=10, max_length=10)
    gender: Literal["male", "female"]

    _normalize_phone = field_validator("phone")(_phone_validator)

class RegisterInitResponse(BaseModel):
    status: str
    message: str
    expires_in_seconds: int
    bot_link: Optional[str] = None

class RegisterVerifyRequest(BaseModel):
    phone: str
    code: str = Field(pattern=OTP_CODE_PATTERN)

    _normalize_phone = field_validator("phone")(_phone_validator)

class RegisterResponse(BaseModel):
    status: str
    token: Optional[Token] = None

class ForgotPasswordRequest(BaseModel):
    phone: str

    _normalize_phone = field_validator("phone")(_phone_validator)

class ResetPasswordRequest(BaseModel):
    phone: str
    code: str = Field(pattern=OTP_CODE_PATTERN)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)

    _normalize_phone = field_validator("phone")(_phone_validator)

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)

# Telegram registration bot schemas
class TelegramRegisterStartRequest(BaseModel):
    start_token: str
    telegram_user_id: int
    chat_id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    photo_url: Optional[str] = None

class TelegramRegisterStartResponse(BaseModel):
    status: str
    requires_contact: bool = True

class TelegramRegisterContactRequest(BaseModel):
    # Ixtiyoriy: bot holatsiz, telegram_user_id bo'yicha ham topiladi.
    start_token: Optional[str] = None
    telegram_user_id: int
    chat_id: str
    phone: str

    _normalize_phone = field_validator("phone")(_phone_validator)

class TelegramRegisterContactResponse(BaseModel):
    status: str
    verified: bool = False

class TelegramRegisterCodeRequest(BaseModel):
    # Ixtiyoriy: bot holatsiz, telegram_user_id bo'yicha ham topiladi.
    start_token: Optional[str] = None
    telegram_user_id: int

class TelegramRegisterCodeResponse(BaseModel):
    status: str
    code: Optional[str] = None
