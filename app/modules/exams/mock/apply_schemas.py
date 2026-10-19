from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from .applicant_rules import validate_passport, validate_phone

# ─── Applicant ───

class ApplicantRegisterInput(BaseModel):
    first_name: str
    last_name: str
    patronymic: Optional[str] = None
    address: Optional[str] = Field(None, max_length=120)  # Region'ga normallashtiriladi
    gender: str          # M | F
    birth_date: Optional[str] = None   # YYYY-MM-DD
    passport_series: Optional[str] = None
    phone: Optional[str] = None
    # Faqat /apply/{slug}/photo orqali yuklangan rasm URL'i (boshqa havola rad etiladi).
    photo_url: Optional[str] = Field(None, max_length=300)
    # CAPTCHA (APPLY_CAPTCHA_ENABLED bo'lganda majburiy).
    captcha_token: Optional[str] = Field(None, max_length=2048)

    @field_validator('passport_series')
    @classmethod
    def check_passport(cls, v: Optional[str]):
        if v and not validate_passport(v):
            raise ValueError("Pasport seriyasi noto'g'ri (AA yoki AD + 7 raqam, masalan AD1234567)")
        return v.upper().strip() if v else v

    @field_validator('phone')
    @classmethod
    def check_phone(cls, v: Optional[str]):
        if v and not validate_phone(v):
            raise ValueError("Telefon raqam noto'g'ri O'zbek format (+998XXXXXXXXX)")
        return v

    @field_validator('gender')
    @classmethod
    def check_gender(cls, v: str):
        if v.upper() not in ('M', 'F'):
            raise ValueError("Jins M (erkak) yoki F (ayol) bo'lishi kerak")
        return v.upper()


class ApplicantResponse(BaseModel):
    id: str
    session_id: int
    exam_id: str
    first_name: str
    last_name: str
    patronymic: Optional[str] = None
    address: Optional[str] = None
    gender: str
    birth_date: Optional[str] = None
    passport_series: Optional[str] = None
    phone: Optional[str] = None
    photo_url: Optional[str] = None
    status: str
    checked_in_at: Optional[datetime] = None
    created_at: datetime

    model_config = {'from_attributes': True}


class ApplySessionPublic(BaseModel):
    """Ochiq ariza sahifasi uchun sessiya ma'lumoti (GET /apply/{slug})."""
    delivery_mode: str
    id: int
    name: str
    exam_type: str
    scheduled_at: datetime
    location: str
    duration_minutes: int
    payment_details: Optional[str] = None  # erkin matn: to'lov rekvizitlari
