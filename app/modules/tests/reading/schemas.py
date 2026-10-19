from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from typing import List, Optional
from enum import Enum
from datetime import datetime

class ReadingQuestionType(str, Enum):
    GAP_FILL = "GAP_FILL"
    GAP_FILL_FILL = "GAP_FILL_FILL"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    TRUE_FALSE_NOT_GIVEN = "TRUE_FALSE_NOT_GIVEN"
    HEADINGS_MATCH = "HEADINGS_MATCH"
    TEXT_MATCH = "TEXT_MATCH"
    MULTIPLE_SELECT = "MULTIPLE_SELECT"

# --- OPTIONS ---
class ReadingOptionBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    label: str
    value: str

class ReadingOptionCreate(ReadingOptionBase):
    pass

class ReadingOptionResponse(ReadingOptionBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# --- QUESTIONS ---
class ReadingQuestionBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    question_number: int
    type: ReadingQuestionType
    text: str
    word_limit: int = 0

class ReadingQuestionCreate(ReadingQuestionBase):
    correct_answer: List[str]
    options: List[ReadingOptionCreate] = Field(default_factory=list)

class ReadingQuestionResponse(ReadingQuestionBase):
    id: int
    options: List[ReadingOptionResponse] = Field(default_factory=list)
    # correct_answer bu yerda yo'q (xavfsizlik uchun)

    model_config = ConfigDict(from_attributes=True)

# --- PARTS ---
class ReadingPartBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    title: str
    description: Optional[str] = None
    passage: str
    image_url: Optional[str] = Field(None, max_length=500)

class ReadingPartCreate(ReadingPartBase):
    questions: List[ReadingQuestionCreate]

class ReadingPartResponse(ReadingPartBase):
    id: int
    questions: List[ReadingQuestionResponse]

    model_config = ConfigDict(from_attributes=True)

# --- TEST (EXAM) ---
class ReadingTestBase(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=64)
    title: str
    cefr_level: str
    language: str = "en"
    duration_minutes: int = 60
    total_questions: Optional[int] = None
    
    is_demo: bool = False
    is_free: bool = False
    is_practice: bool = False
    is_active: bool = True

class ReadingTestCreate(ReadingTestBase):
    # NOTE: `id` is NOT accepted from the client — it is auto-generated server-side.
    parts: List[ReadingPartCreate]

class ReadingTestUpdate(BaseModel):
    title: Optional[str] = None
    cefr_level: Optional[str] = None
    duration_minutes: Optional[int] = None
    total_questions: Optional[int] = None
    is_demo: Optional[bool] = None
    is_free: Optional[bool] = None
    is_practice: Optional[bool] = None
    is_active: Optional[bool] = None
    parts: Optional[List[ReadingPartCreate]] = None

class ReadingTestResponse(ReadingTestBase):
    id: str
    created_at: datetime
    parent_test_id: Optional[str] = None
    source_part_id: Optional[int] = None
    practice_part_number: Optional[int] = None
    parts: List[ReadingPartResponse]

    model_config = ConfigDict(from_attributes=True)

# --- ADMIN: javoblar (correct_answer) bilan ---
# Ommaviy `ReadingTestResponse` xavfsizlik uchun correct_answer'ni YASHIRADI.
# Admin ko'rish/tahrirlash oynasi esa javoblarni ko'rishi va qayta yuborishi
# kerak (PUT parts[].questions[].correct_answer majburiy). Shuning uchun admin
# endpointlar shu schemalardan foydalanadi. Studentga HECH QACHON berilmaydi.
class ReadingQuestionAdminResponse(ReadingQuestionResponse):
    correct_answer: List[str] = Field(default_factory=list)

    @field_validator("correct_answer", mode="before")
    @classmethod
    def _normalize_correct_answer(cls, v):
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [str(x) for x in v]
        return [str(v)]

class ReadingPartAdminResponse(ReadingPartResponse):
    questions: List[ReadingQuestionAdminResponse]

class ReadingTestAdminResponse(ReadingTestResponse):
    parts: List[ReadingPartAdminResponse]

# --- RESULTS & SUBMISSION ---
class UserAnswer(BaseModel):
    # Klient savolni DB id (`question_id`) yoki savol raqami (`question_number`)
    # bilan aniqlashi mumkin — kamida bittasi majburiy.
    question_id: Optional[int] = None
    question_number: Optional[int] = None
    answers: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_identifier(self):
        if self.question_id is None and self.question_number is None:
            raise ValueError("question_id yoki question_number yuborilishi shart")
        return self

class ReadingSubmitRequest(BaseModel):
    answers: List[UserAnswer]
    exam_attempt_id: Optional[int] = None

class ReadingResultResponse(BaseModel):
    id: int
    exam_id: str = Field(validation_alias="test_id")
    correct_answers: int = Field(validation_alias="raw_score")
    # ILGARI: qattiq yozilgan standart qiymat (35) — ReadingResult'da
    # total_questions ustuni umuman yo'q edi, shuning uchun bu maydon
    # HAQIQIY test savollar sonidan qat'iy nazar har doim 35 ko'rsatardi.
    # Endi model ustuni orqali to'g'ridan-to'g'ri to'ldiriladi.
    total_questions: int
    raw_score: int
    standard_score: float
    cefr_level: Optional[str]
    percentage: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ReadingQuestionReview(BaseModel):
    question_number: int
    question_text: Optional[str] = None
    part_title: Optional[str] = None
    user_answer: List[str]
    correct_answer: List[str] # Bu yerda test_id emas, to'g'ri javoblar ro'yxati
    is_correct: bool
    type: str

class ReadingResultDetailResponse(BaseModel):
    summary: ReadingResultResponse
    review: List[ReadingQuestionReview]