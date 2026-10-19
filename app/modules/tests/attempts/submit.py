"""Eski `/submit` kontrakti dispatcher'i: mock urinish bo'lsa exams, aks holda practice."""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tests.attempts.common import SkillSpec


async def submit_with_answers(
    db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, test, exam_attempt_id: Optional[int],
    incoming: Dict[int, List[str]],
):
    from app.modules.exams.attempts import submit_mock_with_answers
    from app.modules.practice.service import submit_practice_with_answers

    result = await submit_mock_with_answers(db, spec, user_id, test, exam_attempt_id, incoming)
    if result is not None:
        return result
    return await submit_practice_with_answers(db, spec, user_id, test, incoming)
