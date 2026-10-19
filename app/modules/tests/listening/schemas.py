from pydantic import AliasChoices, BaseModel, Field, ConfigDict, computed_field, model_validator
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum

class ListeningQuestionType(str, Enum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    GAP_FILL = "GAP_FILL"
    MATCHING = "MATCHING"
    MAP_DIAGRAM = "MAP_DIAGRAM"
    SENTENCE_COMPLETION = "SENTENCE_COMPLETION"
    SHORT_ANSWER = "SHORT_ANSWER"

# --- OPTIONS ---
class OptionBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    value: str
    label: str

class OptionCreate(OptionBase):
    @model_validator(mode="before")
    @classmethod
    def normalize_option(cls, value):
        if isinstance(value, str):
            return {"label": "", "value": value}
        if isinstance(value, dict):
            label = value.get("label") or value.get("key") or value.get("letter") or ""
            option_value = value.get("value") or value.get("text") or value.get("content") or ""
            return {"label": str(label), "value": str(option_value)}
        return value

class OptionResponse(OptionBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# --- QUESTIONS ---
class ListeningQuestionBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    question_number: int = Field(..., alias="question_number")
    type: ListeningQuestionType
    text: Optional[str] = Field(None, alias="question")
    
    model_config = ConfigDict(populate_by_name=True)

class ListeningQuestionCreate(ListeningQuestionBase):
    correct_answer: str = Field(..., alias="correct_answer")
    options: Optional[List[OptionCreate]] = []

    @model_validator(mode="before")
    @classmethod
    def normalize_question(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        # Common JSON aliases.
        if data.get("text") is None and data.get("question") is not None:
            data["text"] = data["question"]
        qtype = str(data.get("type") or "").strip().upper()
        aliases = {
            "TEXT_MATCH": "MATCHING",
            "MAP_MATCH": "MAP_DIAGRAM",
            "HEADINGS_MATCH": "MATCHING",
            "MULTIPLE_CHOICE_ABC": "MULTIPLE_CHOICE",
            "MCQ": "MULTIPLE_CHOICE",
            "GAP": "GAP_FILL",
            "GAP-FILL": "GAP_FILL",
        }
        data["type"] = aliases.get(qtype, qtype)
        answer = data.get("correct_answer")
        if isinstance(answer, (list, tuple, set)):
            data["correct_answer"] = " / ".join(str(x).strip() for x in answer if str(x).strip())
        elif answer is not None:
            data["correct_answer"] = str(answer).strip()
        options = data.get("options") or data.get("choices") or []
        normalized_options = []
        for idx, opt in enumerate(options):
            if isinstance(opt, str):
                normalized_options.append({"label": chr(65 + idx), "value": opt})
            elif isinstance(opt, dict):
                item = dict(opt)
                label = item.get("label") or item.get("key") or item.get("letter")
                value = item.get("value") or item.get("text") or item.get("content")
                item["label"] = str(label or chr(65 + idx))
                item["value"] = str(value or "")
                normalized_options.append(item)
        data["options"] = normalized_options
        return data

class ListeningQuestionResponse(ListeningQuestionBase):
    id: int
    options: List[OptionResponse] = []
    # correct_answer bu yerda yo'q (xavfsizlik uchun)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# --- PARTS ---
class ListeningPartBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    part_number: int = Field(..., alias="part_number")
    title: str
    instruction: str = ""
    task_type: str = Field("", alias="task_type")
    # Ilgari `alias="audio_label"` edi: javobda part audio'si `audio_url` emas
    # `audio_label` kaliti bilan kelardi va admin formasi uni ko'rmasdi (PUT'da
    # audio yo'qolardi). Endi javob `audio_url` bilan keladi; kiruvchi JSON'da
    # ikkala nom ham qabul qilinadi. Eski klientlar uchun javobda `audio_label`
    # ham saqlanadi (ListeningPartResponse.audio_label).
    audio_url: Optional[str] = Field(None, validation_alias=AliasChoices("audio_url", "audio_label"))
    audio_start_seconds: Optional[float] = Field(None, ge=0)
    audio_end_seconds: Optional[float] = Field(None, ge=0)
    context: Optional[str] = None
    passage: Optional[str] = ""
    map_image: Optional[str] = Field(None, alias="map_image")

    model_config = ConfigDict(populate_by_name=True)

class ListeningPartCreate(ListeningPartBase):
    questions: List[ListeningQuestionCreate]

    @model_validator(mode="after")
    def normalize_part(self):
        if self.audio_start_seconds is not None and self.audio_end_seconds is not None and self.audio_end_seconds <= self.audio_start_seconds:
            raise ValueError("audio_end_seconds audio_start_seconds dan katta bo'lishi kerak")
        defaults = {
            1: "You will hear some sentences. You will hear each sentence twice. Choose the correct reply to each sentence (A, B, or C).",
            2: "You will hear someone giving a talk. For each question, fill in the missing information in the numbered space. Write ONE WORD and / or A NUMBER for each answer.",
            3: "You will hear people speaking in different situations. Match each speaker (15-18) to the place where the speaker is (A-F). There are TWO EXTRA places which you do not need to use.",
            4: "You will hear someone giving a talk. Label the places (19-23) on the map (A-H). There are THREE extra options which you do not need to use.",
            5: "You will hear three extracts. Choose the correct answer (A, B or C) for each question (24-29). There are TWO questions for each extract.",
            6: "You will hear a part of a lecture. For each question, fill in the missing information in the numbered space. Write no more than ONE WORD for each answer.",
        }
        if not self.instruction:
            self.instruction = defaults.get(self.part_number, "")
        if not self.task_type:
            if self.part_number in (1, 5): self.task_type = "MULTIPLE_CHOICE"
            elif self.part_number == 3: self.task_type = "MATCHING"
            elif self.part_number == 4: self.task_type = "MAP_DIAGRAM"
            else: self.task_type = "GAP_FILL"
        # Part-level matching options: strings become A/B/C... labels.
        normalized = []
        for idx, opt in enumerate(self.options or []):
            if not opt.label:
                opt.label = chr(65 + idx)
            normalized.append(opt)
        self.options = normalized
        return self
    options: Optional[List[OptionCreate]] = []

class ListeningPartResponse(ListeningPartBase):
    id: int
    questions: List[ListeningQuestionResponse]
    options: List[OptionResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def audio_label(self) -> Optional[str]:
        """Orqaga moslik: eski klientlar `audio_label` kalitini o'qiydi."""
        return self.audio_url

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# --- EXAM ---
class ListeningAudioMode(str, Enum):
    EXAM = "EXAM"
    PARTS = "PARTS"


class ListeningExamBase(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=64)
    title: str
    is_demo: bool = Field(False, alias="is_demo")
    is_free: bool = Field(False, alias="is_free")
    is_practice: bool = Field(False, alias="is_practice")
    is_active: bool = Field(True, alias="is_active")
    
    cefr_level: str = Field(..., alias="level")
    duration_minutes: int = Field(35, alias="duration")
    total_questions: Optional[int] = Field(None, alias="total_questions")
    sections: str = ""
    audio_mode: ListeningAudioMode = ListeningAudioMode.PARTS
    audio_url: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

class ListeningExamCreate(ListeningExamBase):
    # NOTE: `id` is NOT accepted from the client — it is auto-generated server-side.
    parts: List[ListeningPartCreate]

class ListeningExamUpdate(BaseModel):
    title: Optional[str] = None
    is_demo: Optional[bool] = None
    is_free: Optional[bool] = None
    is_practice: Optional[bool] = None
    is_active: Optional[bool] = None
    
    cefr_level: Optional[str] = Field(None, alias="level")
    duration_minutes: Optional[int] = Field(None, alias="duration")
    total_questions: Optional[int] = Field(None, alias="total_questions")
    sections: Optional[str] = None
    audio_mode: Optional[ListeningAudioMode] = None
    audio_url: Optional[str] = None
    parts: Optional[List[ListeningPartCreate]] = None

    model_config = ConfigDict(populate_by_name=True)

class ListeningExamResponse(ListeningExamBase):
    id: str
    parent_test_id: Optional[str] = None
    practice_part_number: Optional[int] = None
    parts: List[ListeningPartResponse]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# --- ADMIN: javoblar (correct_answer) bilan ---
# Ommaviy `ListeningExamResponse` correct_answer'ni yashiradi (xavfsizlik).
# Admin ko'rish/tahrirlash uchun alohida schema — studentga berilmaydi.
class ListeningQuestionAdminResponse(ListeningQuestionResponse):
    correct_answer: str = ""

class ListeningPartAdminResponse(ListeningPartResponse):
    questions: List[ListeningQuestionAdminResponse]

class ListeningExamAdminResponse(ListeningExamResponse):
    parts: List[ListeningPartAdminResponse]

# --- RESULTS & SUBMISSION ---
class ListeningSubmission(BaseModel):
    exam_id: str
    user_answers: Dict[str, str] = Field(..., alias="user_answers")
    exam_attempt_id: Optional[int] = Field(None, alias="exam_attempt_id")

class ListeningResultResponse(BaseModel):
    id: int
    exam_id: str
    raw_score: int
    standard_score: float
    cefr_level: Optional[str]
    percentage: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ListeningQuestionReview(BaseModel):
    question_number: int
    user_answer: Optional[str] = ""
    correct_answer: str 
    is_correct: bool
    type: ListeningQuestionType

    model_config = ConfigDict(from_attributes=True)

class ListeningResultDetailResponse(BaseModel):
    summary: ListeningResultResponse
    review: List[ListeningQuestionReview]

    model_config = ConfigDict(from_attributes=True)