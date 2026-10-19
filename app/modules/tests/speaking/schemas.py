import logging
import uuid
from datetime import datetime
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

# external IDlar diskdagi yo'l qismi sifatida ham ishlatiladi (recording draft
# papkalari), shuning uchun faqat xavfsiz belgilar: "../x" kabilar rad etiladi.
Id = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
]
ImageUrl = Annotated[str, Field(min_length=1, max_length=500, pattern=r"^(https?://|/)")]
Question = Annotated[str, Field(min_length=1)]
Point = Annotated[str, Field(min_length=1, max_length=300)]

_TIMING_KEYS = frozenset({"prep_seconds", "answer_seconds", "describe_answer_seconds"})


class _Create(BaseModel):
    """Admin faqat KONTENT kiritadi. Vaqtlar speaking_config.py dan olinadi.

    Eski JSON fayllarda prep_seconds/answer_seconds bo'lishi mumkin: ular
    ogohlantirish bilan tashlab yuboriladi (import buzilmaydi), lekin
    qiymatlari e'tiborga olinmaydi.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_timing_fields(cls, values):
        if isinstance(values, dict):
            dropped = _TIMING_KEYS & values.keys()
            if dropped:
                logger.warning(
                    "Speaking JSON: %s maydonlari e'tiborga olinmadi (vaqtlar "
                    "speaking_config.py dan olinadi)",
                    sorted(dropped),
                )
                return {k: v for k, v in values.items() if k not in _TIMING_KEYS}
        return values


# ---------------------------------------------------------------------------
# PART 1.1 (A1-A2)
# ---------------------------------------------------------------------------
class SpeakingPart11Create(_Create):
    external_id: Optional[Id] = None  # None bo'lsa serverda standart ID beriladi
    question_1_id: Optional[Id] = None
    question_2_id: Optional[Id] = None
    question_3_id: Optional[Id] = None
    question_1: Question
    question_2: Question
    question_3: Question


# ---------------------------------------------------------------------------
# PART 1.2 (B1)
# ---------------------------------------------------------------------------
class SpeakingPart12Create(_Create):
    external_id: Optional[Id] = None
    image_1_url: ImageUrl
    image_2_url: ImageUrl
    question_describe_id: Optional[Id] = None
    question_2_id: Optional[Id] = None
    question_3_id: Optional[Id] = None
    question_describe: Question  # ikki rasmni solishtirish (45s)
    question_2: Question  # 30s
    question_3: Question  # 30s


# ---------------------------------------------------------------------------
# PART 2 (B2)
# ---------------------------------------------------------------------------
class SpeakingPart2Create(_Create):
    external_id: Optional[Id] = None
    question_1_id: Optional[Id] = None
    question_2_id: Optional[Id] = None
    question_3_id: Optional[Id] = None
    image_url: ImageUrl
    # Rasmni tasvirlash SO'RALMAYDI (Part 1.2 dan asosiy farq).
    question_1: Question
    question_2: Question
    question_3: Question


# ---------------------------------------------------------------------------
# PART 3 (C1)
# ---------------------------------------------------------------------------
class SpeakingPart3Create(_Create):
    external_id: Optional[Id] = None
    topic_id: Optional[Id] = None
    topic: Question  # bahsli da'vo: "Citizens should be allowed to ..."
    # Talabgor har ro'yxatdan 2 tadan tanlaydi → har birida aniq 3 ta bo'lishi kerak
    for_points: List[Point] = Field(min_length=3, max_length=3)
    against_points: List[Point] = Field(min_length=3, max_length=3)


# ---------------------------------------------------------------------------
# RESPONSE modellari: Create dan MEROS OLMAYDI.
# Aks holda kirish cheklovlari chiqishga ham qo'llanib, bazadagi eski yozuv
# (masalan 2 ta point) serializatsiyada 500 beradi.
# external_id lar legacy qatorlarda NULL bo'lishi mumkin — backfill qilingach
# majburiy (str) qiling.
# ---------------------------------------------------------------------------
class _PartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    external_id: Optional[str] = None
    prep_seconds: int
    answer_seconds: int


class SpeakingPart11Response(_PartResponse):
    question_1_id: Optional[str] = None
    question_2_id: Optional[str] = None
    question_3_id: Optional[str] = None
    question_1: str
    question_2: str
    question_3: str


class SpeakingPart12Response(_PartResponse):
    image_1_url: str
    image_2_url: str
    question_describe_id: Optional[str] = None
    question_2_id: Optional[str] = None
    question_3_id: Optional[str] = None
    question_describe: str
    question_2: str
    question_3: str
    describe_answer_seconds: int


class SpeakingPart2Response(_PartResponse):
    image_url: str
    question_1_id: Optional[str] = None
    question_2_id: Optional[str] = None
    question_3_id: Optional[str] = None
    question_1: str
    question_2: str
    question_3: str


class SpeakingPart3Response(_PartResponse):
    topic_id: Optional[str] = None
    topic: str
    for_points: List[str]
    against_points: List[str]


# ---------------------------------------------------------------------------
# SPEAKING TEST (to'liq, admin CRUD uchun)
# ---------------------------------------------------------------------------
class SpeakingTestCreate(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    is_demo: bool = False
    is_free: bool = False
    is_practice: bool = False
    is_active: bool = True

    # Qismlarga ajratilgan (part-only) test: faqat kerakli qism(lar) yuboriladi.
    part_1_1: Optional[SpeakingPart11Create] = None
    part_1_2: Optional[SpeakingPart12Create] = None
    part_2: Optional[SpeakingPart2Create] = None
    part_3: Optional[SpeakingPart3Create] = None

    @model_validator(mode="after")
    def _check_parts(self):
        names = ("part_1_1", "part_1_2", "part_2", "part_3")
        present = [n for n in names if getattr(self, n) is not None]
        if not present:
            raise ValueError("Kamida bitta speaking qismi (part_1_1/part_1_2/part_2/part_3) kerak")
        missing = [n for n in names if n not in present]
        if missing and not self.is_practice:
            if "is_practice" in self.model_fields_set:
                raise ValueError(f"To'liq (is_practice=false) testda barcha 4 qism kerak. Yetishmayapti: {missing}")
            self.is_practice = True  # part-only test avtomatik practice
        return self


class SpeakingTestUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    is_demo: Optional[bool] = None
    is_free: Optional[bool] = None
    is_practice: Optional[bool] = None
    is_active: Optional[bool] = None

    # Yozuvlari bor testda qism kontentini o'zgartirib bo'lmaydi (409).
    part_1_1: Optional[SpeakingPart11Create] = None
    part_1_2: Optional[SpeakingPart12Create] = None
    part_2: Optional[SpeakingPart2Create] = None
    part_3: Optional[SpeakingPart3Create] = None


class SpeakingTestListItem(BaseModel):
    """Ro'yxat ko'rinishi uchun yengil model."""

    id: str
    title: str
    is_active: bool
    is_demo: bool
    is_free: bool
    is_practice: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpeakingTestResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    is_demo: bool
    is_free: bool
    is_practice: bool
    is_active: bool
    created_at: datetime

    part_1_1: Optional[SpeakingPart11Response] = None
    part_1_2: Optional[SpeakingPart12Response] = None
    part_2: Optional[SpeakingPart2Response] = None
    part_3: Optional[SpeakingPart3Response] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# ATTEMPT UCHUN TASODIFIY TANLANGAN TEST
# ---------------------------------------------------------------------------
class SpeakingAssignedTestResponse(BaseModel):
    """Foydalanuvchiga imtihonda ko'rsatiladigan (tasodifiy tanlangan) test."""

    skill_attempt_id: int
    speaking_test: SpeakingTestResponse

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# SUBMISSION / RESULT (audio yuklash + qo'lda baholash)
# ---------------------------------------------------------------------------
class SpeakingSubmissionResponse(BaseModel):
    id: int
    user_id: uuid.UUID
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    username: Optional[str] = None
    birth_date: Optional[datetime] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    speaking_test_id: str
    question_external_id: Optional[str] = None
    part_external_id: Optional[str] = None
    mock_skill_attempt_id: Optional[int] = None
    practice_attempt_id: Optional[int] = None
    audio_url: str
    duration_seconds: Optional[int] = None
    status: str  # pending | checked | draft
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpeakingRecordingItem(BaseModel):
    """Urinish ichidagi bitta savol-yozuv (baholovchi uchun tinglash ro'yxati)."""

    id: int
    part_external_id: Optional[str] = None
    question_external_id: Optional[str] = None
    audio_url: str
    duration_seconds: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpeakingAttemptPendingResponse(BaseModel):
    """BITTA urinish = BITTA baholash.

    Baholovchi urinishdagi barcha yozuvlarni tinglab, UMUMIY bitta ball kiritadi
    (savollar alohida baholanmaydi). Natijani yuborish uchun `submission_id`
    (urinishning birinchi yozuvi) ishlatiladi: POST .../submissions/{submission_id}/result.
    """

    submission_id: int
    mock_skill_attempt_id: Optional[int] = None
    practice_attempt_id: Optional[int] = None
    user_id: uuid.UUID
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    username: Optional[str] = None
    speaking_test_id: str
    recordings_count: int
    total_duration_seconds: Optional[int] = None
    recordings: List[SpeakingRecordingItem]
    created_at: datetime


class SpeakingRecordingDraftResponse(BaseModel):
    id: int
    mock_skill_attempt_id: int
    speaking_test_id: str
    part_external_id: str
    question_external_id: str
    next_chunk_index: int
    total_bytes: int
    duration_seconds: Optional[int] = None
    status: str  # recording | finalized
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpeakingResultCreate(BaseModel):
    # Mezonlar shkalasi hali rasman belgilanmagan (0-9 yoki 0-75). Shu
    # sababli 0-75 (kengroq) bilan cheklandi; shkala hal bo'lgach toraytiring.
    fluency_coherence: Optional[float] = Field(None, ge=0, le=75)
    lexical_resource: Optional[float] = Field(None, ge=0, le=75)
    grammar_accuracy: Optional[float] = Field(None, ge=0, le=75)
    pronunciation: Optional[float] = Field(None, ge=0, le=75)
    # Gapirish bo'yicha 3 turdagi topshiriqning umumlashgan raw bali (0–36).
    # Yakuniy ball (0–75) server tomonida jadval bo'yicha hisoblanadi (scoring_policy).
    overall_raw: float = Field(..., ge=0, le=36)
    # get_cefr_level qaytaradigan qiymatlar to'plami ko'rinmagani uchun
    # Literal qilinmadi; faqat uzunlik cheklangan.
    cefr_level: Optional[str] = Field(None, max_length=32)
    comment: Optional[str] = None


class SpeakingResultResponse(BaseModel):
    # Create dan meros OLMAYDI: kirish cheklovlari (ge/le) chiqishda
    # eski yozuvlarni 500 ga aylantirmasligi uchun.
    id: int
    submission_id: int
    evaluator_id: Optional[uuid.UUID] = None  # users.id — UUID
    fluency_coherence: Optional[float] = None
    lexical_resource: Optional[float] = None
    grammar_accuracy: Optional[float] = None
    pronunciation: Optional[float] = None
    overall_band: Optional[float] = None
    cefr_level: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)