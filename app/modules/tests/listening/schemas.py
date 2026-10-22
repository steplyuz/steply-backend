"""Listening Pydantic sxemalari (request / response).

Eslatma: ORM modellar `.models` da. Bu fayl faqat API kontraktini belgilaydi.
Admin javoblari (`*AdminResponse`) `correct_answer` ni o'z ichiga oladi; talabaga
qaytariladigan sxemalarda `correct_answer` YO'Q (xavfsizlik uchun).
"""
import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .models import ListeningQuestionType


class ListeningAudioMode(str, Enum):
    EXAM = "EXAM"    # bitta umumiy audio, har bir part uchun start/end segment
    PARTS = "PARTS"  # har bir part uchun alohida audio


# ---------------------------------------------------------------------------
# Yordamchi normalizatorlar
# ---------------------------------------------------------------------------
def _normalize_options(value: Any) -> Any:
    """Options: ["A", "B"] yoki [{"label": ..., "value": ...}] — ikkalasini qabul qiladi."""
    if value is None:
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            out.append({"label": item, "value": item})
        else:
            out.append(item)
    return out


def _normalize_correct_answer(value: Any) -> str:
    """correct_answer: "A" yoki ["A"] yoki ["A", "B"] -> "A/B".

    Baholash (get_result_with_review) javoblarni "/" bo'yicha ajratadi, shuning
    uchun ro'yxat shu formatda saqlanadi.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "/".join(str(x).strip() for x in value if str(x).strip())
    return str(value)


# ---------------------------------------------------------------------------
# OPTIONS
# ---------------------------------------------------------------------------
class ListeningOptionCreate(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    label: str
    value: str


class ListeningOptionResponse(BaseModel):
    id: int
    external_id: Optional[str] = None
    label: str
    value: str

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# QUESTIONS
# ---------------------------------------------------------------------------
class ListeningQuestionCreate(BaseModel):
    # Import JSON'da savol matni `question` yoki `text` kalitida keladi.
    model_config = ConfigDict(populate_by_name=True)

    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    question_number: int
    type: ListeningQuestionType
    text: Optional[str] = Field(None, validation_alias=AliasChoices("text", "question"))
    group_title: Optional[str] = Field(None, max_length=200)
    correct_answer: str = ""
    options: List[ListeningOptionCreate] = Field(default_factory=list)

    @field_validator("correct_answer", mode="before")
    @classmethod
    def _norm_correct_answer(cls, v):
        return _normalize_correct_answer(v)

    @field_validator("options", mode="before")
    @classmethod
    def _norm_options(cls, v):
        return _normalize_options(v)


class ListeningQuestionResponse(BaseModel):
    """Talaba uchun: correct_answer yo'q."""
    id: int
    external_id: Optional[str] = None
    question_number: int
    type: ListeningQuestionType
    text: Optional[str] = None
    group_title: Optional[str] = None
    options: List[ListeningOptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ListeningQuestionAdminResponse(ListeningQuestionResponse):
    """Admin uchun: correct_answer bilan."""
    correct_answer: str = ""


# ---------------------------------------------------------------------------
# PARTS
# ---------------------------------------------------------------------------
class ListeningPartCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    part_number: int
    title: str
    instruction: str = ""
    task_type: str = ""
    audio_url: Optional[str] = Field("", max_length=500)
    audio_start_seconds: Optional[float] = None
    audio_end_seconds: Optional[float] = None
    context: Optional[str] = None
    passage: Optional[str] = None
    map_image: Optional[str] = None
    image_url: Optional[str] = Field(None, max_length=500)
    example_json: Optional[str] = None
    options: List[ListeningOptionCreate] = Field(default_factory=list)
    questions: List[ListeningQuestionCreate] = Field(default_factory=list)

    @field_validator("example_json", mode="before")
    @classmethod
    def _norm_example_json(cls, v):
        # Import JSON'da example obyekt bo'lib kelishi mumkin; DB'ga matn sifatida saqlanadi.
        if v is None or isinstance(v, str):
            return v
        return json.dumps(v, ensure_ascii=False)

    @field_validator("options", mode="before")
    @classmethod
    def _norm_options(cls, v):
        return _normalize_options(v)


class ListeningPartResponse(BaseModel):
    id: int
    external_id: Optional[str] = None
    part_number: int
    title: str
    instruction: Optional[str] = None
    task_type: Optional[str] = None
    audio_url: Optional[str] = None
    audio_start_seconds: Optional[float] = None
    audio_end_seconds: Optional[float] = None
    context: Optional[str] = None
    passage: Optional[str] = None
    map_image: Optional[str] = None
    image_url: Optional[str] = None
    example_json: Optional[str] = None
    options: List[ListeningOptionResponse] = Field(default_factory=list)
    questions: List[ListeningQuestionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ListeningPartAdminResponse(ListeningPartResponse):
    questions: List[ListeningQuestionAdminResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TEST (EXAM)
# ---------------------------------------------------------------------------
class ListeningExamBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    cefr_level: str = Field("B2", validation_alias=AliasChoices("cefr_level", "level"))
    duration_minutes: int = Field(35, validation_alias=AliasChoices("duration_minutes", "duration"))
    total_questions: Optional[int] = None
    sections: Optional[str] = None
    audio_mode: ListeningAudioMode = ListeningAudioMode.PARTS
    audio_url: Optional[str] = Field(None, max_length=500)

    is_demo: bool = False
    is_free: bool = False
    is_practice: bool = False
    is_active: bool = True


class ListeningExamCreate(ListeningExamBase):
    # Services `data.model_fields_set` orqali is_practice berilmaganini aniqlaydi,
    # shuning uchun is_practice'ga qiymat tayinlash shart emas (default False).
    id: Optional[str] = Field(None, min_length=1, max_length=64)
    parts: List[ListeningPartCreate] = Field(default_factory=list)


class ListeningExamUpdate(BaseModel):
    # Barcha maydonlar ixtiyoriy: PUT faqat yuborilganini o'zgartiradi
    # (services `model_dump(exclude_unset=True)` ishlatadi).
    title: Optional[str] = None
    cefr_level: Optional[str] = None
    duration_minutes: Optional[int] = None
    total_questions: Optional[int] = None
    sections: Optional[str] = None
    audio_mode: Optional[ListeningAudioMode] = None
    audio_url: Optional[str] = Field(None, max_length=500)
    is_demo: Optional[bool] = None
    is_free: Optional[bool] = None
    is_practice: Optional[bool] = None
    is_active: Optional[bool] = None
    parts: Optional[List[ListeningPartCreate]] = None


class ListeningExamResponse(BaseModel):
    """Talaba uchun test kontenti (correct_answer yo'q)."""
    id: str
    title: str
    cefr_level: Optional[str] = None
    duration_minutes: Optional[int] = None
    total_questions: Optional[int] = None
    sections: Optional[str] = None
    audio_mode: Optional[str] = None
    audio_url: Optional[str] = None
    is_demo: bool = False
    is_free: bool = False
    is_practice: bool = False
    is_active: bool = True
    created_at: Optional[datetime] = None
    parent_test_id: Optional[str] = None
    practice_part_number: Optional[int] = None
    parts: List[ListeningPartResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ListeningExamAdminResponse(ListeningExamResponse):
    """Admin uchun: correct_answer bilan."""
    parts: List[ListeningPartAdminResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SUBMISSION & RESULTS
# ---------------------------------------------------------------------------
class ListeningSubmission(BaseModel):
    # Router URL'dagi exam_id ni body'ga yozadi — shuning uchun maydon o'zgaruvchan bo'lishi kerak.
    exam_id: Optional[str] = None
    exam_attempt_id: Optional[int] = None
    user_answers: Dict[str, Any] = Field(default_factory=dict)


class ListeningResultResponse(BaseModel):
    id: int
    exam_id: str
    exam_attempt_id: Optional[int] = None
    raw_score: float = 0.0
    standard_score: Optional[float] = None
    cefr_level: Optional[str] = None
    total_questions: int = 0
    correct_answers: int = 0
    percentage: float = 0.0
    user_answers: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ListeningQuestionReview(BaseModel):
    question_id: Optional[int] = None
    question_number: int
    user_answer: str = ""
    correct_answer: str = ""
    is_correct: bool = False
    type: str

    @field_validator("type", mode="before")
    @classmethod
    def _enum_to_value(cls, v):
        return v.value if isinstance(v, Enum) else v


class ListeningResultDetailResponse(BaseModel):
    summary: ListeningResultResponse
    review: List[ListeningQuestionReview] = Field(default_factory=list)
