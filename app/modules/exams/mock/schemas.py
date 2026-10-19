from enum import Enum
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import Optional, List, Dict
from datetime import datetime, date
import uuid

# --- 1. ENUMS ---
# Yagona manba: SkillType/RegistrationStatus — .models, Gender/Region —
# app.modules.auth.models. Ilgari ular shu faylda AYNAN nusxa qilib qayta
# e'lon qilingan edi: qiymatlar bir xil bo'lsa ham tiplar boshqa-boshqa
# bo'lib, izinstance/== tekshiruvlari kutilmaganda "False" berardi va
# birini o'zgartirganda ikkinchisi eskirib qolardi.
from app.modules.auth.models import Gender, Region  # noqa: F401  (re-export)

from .models import RegistrationStatus, SkillType  # noqa: F401  (re-export)

# --- 2. BASE MOCK EXAM SCHEMAS ---
class MockExamBase(BaseModel):
    title: str = Field(..., min_length=3, max_length=100)
    description: Optional[str] = None
    image_file: Optional[str] = None
    cefr_level: str = "Multilevel"
    duration_minutes: int = 180
    price: float = 0.0
    is_active: bool = True
    reading_id: Optional[str] = None
    listening_id: Optional[str] = None
    writing_id: Optional[str] = None
    speaking_id: Optional[str] = None

class MockExamCreate(MockExamBase):
    # Mock yaratishda barcha 4 skill testi admin tomonidan aniq tanlanadi.
    reading_id: str
    listening_id: str
    writing_id: str
    speaking_id: str

class MockExamUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_file: Optional[str] = None
    cefr_level: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    is_active: Optional[bool] = None
    reading_id: Optional[str] = None
    listening_id: Optional[str] = None
    writing_id: Optional[str] = None
    speaking_id: Optional[str] = None

# --- 3. RESPONSE SCHEMAS (Frontend uchun asosiy) ---
class UserMockExamResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    image_file: Optional[str] = None
    cefr_level: str
    duration_minutes: int = 180
    price: float
    is_active: bool
    is_purchased: bool = False
    reading_id: Optional[str] = None
    listening_id: Optional[str] = None
    writing_id: Optional[str] = None
    speaking_id: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

# --- 4. PROCESS SCHEMAS (Submission Logic) ---
class MockSkillSubmit(BaseModel):
    raw_score: float = Field(0.0, ge=0, le=75)
    user_answers: Optional[Dict] = Field(default_factory=dict)

class MockSkillAutosaveRequest(BaseModel):
    current_part: Optional[str] = Field(None, max_length=128)
    current_question: Optional[str] = Field(None, max_length=128)
    draft: Optional[Dict] = None
    user_answers: Optional[Dict] = None


class MockSkillAttemptResponse(BaseModel):
    id: int
    attempt_id: int
    user_id: uuid.UUID  # Shu yerga qo'shildi
    skill: SkillType
    raw_score: float
    scaled_score: float 
    cefr_level: Optional[str] = None
    is_checked: bool
    submitted_at: Optional[datetime] = None
    current_part: Optional[str] = None
    current_question: Optional[str] = None
    draft: Optional[Dict] = None
    last_saved_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class MockSkillStatusResponse(BaseModel):
    skill: str
    status: str = "NOT_STARTED"
    is_checked: bool
    is_submitted: bool
    submitted_at: Optional[datetime] = None
    current_part: Optional[str] = None
    current_question: Optional[str] = None
    draft: Optional[Dict] = None
    last_saved_at: Optional[datetime] = None

# --- 5. ATTEMPT & RESULT SCHEMAS ---
class MockExamStartResponse(BaseModel):
    # Endpoint dict ichida `attempt_id` qaytaradi; ORM obyektda esa `id`. Ikkalasi ham qabul qilinadi
    # (ilgari faqat "id" edi va /exams/{id}/start har doim 500 berardi).
    attempt_id: int = Field(..., validation_alias=AliasChoices("attempt_id", "id"))
    mock_exam_id: str
    started_at: datetime
    expires_at: Optional[datetime] = None
    server_time: Optional[datetime] = None
    remaining_seconds: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class MockExamAttemptDetail(BaseModel):
    id: int
    registration_id: Optional[int] = None
    session_id: Optional[int] = None
    status: Optional[str] = None
    mock_exam_id: str
    user_id: uuid.UUID  # Shu yerga qo'shildi
    started_at: datetime
    expires_at: Optional[datetime] = None
    allowed_duration_seconds: Optional[int] = None
    is_finished: bool
    skills: List[MockSkillAttemptResponse]
    
    model_config = ConfigDict(from_attributes=True)

class MockExamResultResponse(BaseModel):
    id: int
    attempt_id: int
    user_id: uuid.UUID
    reading_ball: float
    listening_ball: float
    writing_ball: float
    speaking_ball: float
    overall_score: float
    cefr_level: str
    status: str = "FINALIZED"
    overall_status: str = "FINALIZED"
    skill_statuses: Dict[str, str] = Field(default_factory=dict)
    finalized_at: Optional[datetime] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# --- 6. PURCHASE SCHEMAS ---
class MockPurchaseResponse(BaseModel):
    id: int
    user_id: uuid.UUID
    mock_exam_id: str
    is_active: bool
    comment: Optional[str] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# --- 8. EXAM SESSION (Ochiq test sanalari) ---
class DeliveryMode(str, Enum):
    PAPER = "PAPER"        # qog'oz varaq + OMR
    COMPUTER = "COMPUTER"  # kompyuterda (CD) topshirish


class ExamSessionCreate(BaseModel):
    mock_exam_id: str
    exam_date: datetime
    center_name: Optional[str] = None
    room_name: Optional[str] = None
    location_address: str = Field(..., min_length=3)
    region: Optional[Region] = None
    delivery_mode: DeliveryMode = DeliveryMode.PAPER
    capacity: Optional[int] = Field(None, ge=1)
    required_documents: Optional[str] = Field(
        None, description="';' bilan ajratilgan hujjatlar ro'yxati"
    )
    is_active: bool = True
    # True bo'lsa, shu sessiyaga ro'yxatdan o'tish uchun foydalanuvchida
    # avvaldan tasdiqlangan (BillingOrder.status == PAID) to'lov bo'lishi
    # shart — qarang: registration_service.register_for_session. False
    # (standart) — pullik bo'lsa ham, to'lov imtihon markazida naqd olinadi
    # (mavjud xatti-harakat o'zgarmaydi).
    requires_online_payment: bool = False


class ExamSessionUpdate(BaseModel):
    exam_date: Optional[datetime] = None
    delivery_mode: Optional[DeliveryMode] = None
    location_address: Optional[str] = None
    region: Optional[Region] = None
    capacity: Optional[int] = None
    required_documents: Optional[str] = None
    is_active: Optional[bool] = None
    requires_online_payment: Optional[bool] = None


class ExamSessionResponse(BaseModel):
    id: int
    mock_exam_id: str
    exam_date: datetime
    center_name: Optional[str] = None
    room_name: Optional[str] = None
    location_address: str
    region: Optional[str] = None
    registration_slug: Optional[str] = None
    delivery_mode: str = "PAPER"
    capacity: Optional[int] = None
    required_documents: Optional[str] = None
    is_active: bool
    requires_online_payment: bool = False
    registered_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# --- 9. MOCK REGISTRATION (Ro'yxatdan o'tish + imzo) ---
class MockRegistrationCreate(BaseModel):
    session_id: int
    full_name: str = Field(..., min_length=3, max_length=255)
    age: int = Field(..., ge=10, le=100)
    date_of_birth: Optional[date] = None
    gender: Gender
    region: Region
    photo_url: str = Field(..., min_length=1, max_length=2048)


class MockRegistrationResponse(BaseModel):
    id: int
    user_id: uuid.UUID
    session_id: int
    mock_exam_id: str
    full_name: str
    student_id: str
    exam_access_id: str
    qr_token: str
    age: int
    date_of_birth: Optional[date] = None
    gender: Gender
    # str: ochiq /apply/{slug} orqali ro'yxatdan o'tganlarda hudud erkin matn bo'lishi mumkin
    # (Region enum bilan serializatsiya bunday qatorlarda 500 berardi).
    region: str
    photo_url: str
    status: RegistrationStatus
    attended_confirmed_at: Optional[datetime] = None
    billing_order_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- ADMIN HELPER SCHEMAS ---
class MockExamAdminDetail(BaseModel):
    """Admin uchun mock exam to'liq ma'lumotlari (skill test IDlari bilan)."""
    id: str
    title: str
    description: Optional[str] = None
    cefr_level: str
    duration_minutes: int
    price: float
    is_active: bool
    reading_id: Optional[str] = None
    listening_id: Optional[str] = None
    writing_id: Optional[str] = None
    speaking_id: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MockExamCreateWithAutoSelect(BaseModel):
    """
    Admin uchun soddalashtrilgan Mock Exam yaratish sxemasi.
    Agar skill ID lari berilmasa, tizim bazadan avtomatik so'nggi mos testni tanlaydi.
    """
    title: str = Field(..., min_length=3, max_length=100)
    description: Optional[str] = None
    cefr_level: str = "Multilevel"
    duration_minutes: int = 180
    price: float = 0.0
    is_active: bool = True
    # Skill IDlari — agar berilmasa bazadan avtomatik tanlanadi
    reading_id: Optional[str] = None
    listening_id: Optional[str] = None
    writing_id: Optional[str] = None
    speaking_id: Optional[str] = None
