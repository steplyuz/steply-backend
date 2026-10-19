from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class AnswerKeyResponse(BaseModel):
    exam_id: str
    version_number: int
    total_questions: int
    is_locked: bool
    answer_key: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class ExamVersionResponse(BaseModel):
    id: int
    version_number: int
    total_questions: int
    is_locked: bool
    locked_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LockValidationError(BaseModel):
    question_number: Optional[int] = None
    message: str


class LockResultResponse(BaseModel):
    success: bool
    version: Optional[ExamVersionResponse] = None
    errors: List[LockValidationError] = []
