import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# MK ID formati — yagona manba: mock_test/mk_id.py.
# Yangi format: "MK-" + 6 xonali sana + "-" + 3 xonali tasodifiy
# (MK-DDMMYY-NNN). LOGIN paytida ESKI format ("MC-" + 6 xonali sana +
# 1 xonali jins + 2 xonali tasodifiy) ham qabul qilinadi — bazada shu
# formatdagi eski ro'yxatlar hali ham bor va ular ishlashda davom etishi
# kerak (ANY_MK_ID_RE). Yangi ID'lar esa faqat yangi formatda yaratiladi.
from app.modules.exams.mock.mk_id import ANY_MK_ID_RE as _MK_ID_RE


def _normalize_mk_id(v: str) -> str:
    v = v.strip().upper()
    if not _MK_ID_RE.fullmatch(v):
        raise ValueError("MK ID formati noto'g'ri (masalan: MK-290706-785)")
    return v


class ExamIdLoginRequest(BaseModel):
    """Kirish: Student ID (MK ID) YOKI random Exam ID — aynan bittasi."""

    student_id: Optional[str] = Field(None, max_length=32, example="MK-290706-785")
    exam_id: Optional[str] = Field(None, min_length=16, max_length=48, example="EXAM-...")
    # Ikkinchi omil (faqat MK ID bilan kirishda, EXAMS_MK_ID_REQUIRE_PHONE_LAST4 yoqilganda kerak)
    phone_last4: Optional[str] = Field(None, pattern=r"^\d{4}$", example="1234")

    @field_validator("student_id")
    @classmethod
    def _check_student_id(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_mk_id(v) if v is not None else v

    @model_validator(mode="after")
    def _exactly_one(self):
        if bool(self.student_id) == bool(self.exam_id):
            raise ValueError("student_id yoki exam_id dan aynan bittasini yuboring")
        return self


class ExamsTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ExamsStudentResponse(BaseModel):
    id: uuid.UUID
    full_name: Optional[str] = None
    # MK ID endi `mock_registrations.student_id` dan keladi — ya'ni bu
    # qiymat foydalanuvchining emas, JORIY SEANSning identifikatori.
    mk_id: Optional[str] = None
    exam_id: Optional[str] = None
    registration_id: Optional[int] = None
    phone_last4: Optional[str] = None
    # Registratsiya paytida kiritilgan nomzod ma'lumotlari (snapshot) va rasmi —
    # Exams ekranida nomzodni tanish (identifikatsiya) uchun.
    photo_url: Optional[str] = None
    age: Optional[int] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    region: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ExamIdLoginResponse(BaseModel):
    status: str
    token: Optional[ExamsTokenResponse] = None
    student: Optional[ExamsStudentResponse] = None


class ExamsStatusResponse(BaseModel):
    status: str
    message: Optional[str] = None


class EnrolledSessionInfo(BaseModel):
    id: int
    exam_date: datetime
    center_name: Optional[str] = None
    room_name: Optional[str] = None
    location_address: Optional[str] = None


class EnrolledExamResponse(BaseModel):
    id: str
    title: str
    cefr_level: str
    duration_minutes: int
    enrollment_source: str  # hozircha doim "registration"
    mk_id: Optional[str] = None
    session: Optional[EnrolledSessionInfo] = None
    attempt_id: Optional[int] = None
    attempt_status: Optional[str] = None
    is_finished: Optional[bool] = None
    # 4 skill test'ning ID'lari — frontend shu orqali har bir bo'lim
    # kontentini (masalan GET /reading-tests/{id}, PUBLIC) va keyin
    # javobni (POST /exams/reading/{id}/submit) topadi/yuboradi.
    reading_id: Optional[str] = None
    listening_id: Optional[str] = None
    writing_id: Optional[str] = None
    speaking_id: Optional[str] = None


class ExamsListResponse(BaseModel):
    items: List[EnrolledExamResponse]
    total: int