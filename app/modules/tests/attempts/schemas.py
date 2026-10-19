"""Reading/Listening urinish (attempt) uchun umumiy request/response sxemalari."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.modules.tests.attempts.common import normalize_answer


class AnswerSaveRequest(BaseModel):
    """Klient FAQAT nomzodning javobini yuboradi (ball/is_correct/user_id emas).

    Savol `question_id` (DB id) yoki `question_number` bilan aniqlanadi.
    Javob: `answers` (ro'yxat) yoki `answer` (bitta string, Listening uchun qulay).
    """
    question_id: Optional[int] = None
    question_number: Optional[int] = None
    answers: Optional[List[str]] = None
    answer: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.question_id is None and self.question_number is None:
            raise ValueError("question_id yoki question_number yuborilishi shart")
        if self.answers is not None and self.answer is not None:
            # Klient ikkalasini ham yuborsa (masalan, answers=["..."] va answer="..."),
            # answers ustuvor hisoblanadi va xatolik tashlanmaydi.
            self.answer = None
        return self

    def normalized(self) -> List[str]:
        return normalize_answer(self.answers if self.answers is not None else self.answer)


class SavedAnswer(BaseModel):
    question_id: Optional[int] = None
    question_number: int
    answers: List[str] = Field(default_factory=list)
    answer: str = ""
    updated_at: Optional[datetime] = None


class AttemptStateResponse(BaseModel):
    attempt_id: int
    exam_attempt_id: Optional[int] = None   # mock bo'lsa MockExamAttempt.id
    test_id: str
    skill: str
    mode: str = "FULL_PRACTICE"
    part_number: Optional[int] = None
    status: str                             # IN_PROGRESS | SUBMITTED
    started_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    server_time: Optional[datetime] = None
    last_saved_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    remaining_seconds: Optional[int] = None  # ma'lumot uchun; server majburlamaydi
    total_questions: int = 0
    answered_count: int = 0
    result_id: Optional[int] = None
    answers: List[SavedAnswer] = Field(default_factory=list)
