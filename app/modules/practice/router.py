"""Unified public practice-test catalog across all four exam skills."""
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.modules.tests.listening.models import ListeningTest
from app.modules.tests.reading.models import ReadingTest
from app.modules.tests.writing.models import WritingExam
from app.modules.tests.speaking.models import SpeakingTest

router = APIRouter(prefix="/practice", tags=["Practice"])


@router.get("/tests")
async def list_practice_tests(
    skill: str | None = Query(default=None, pattern="^(listening|reading|writing|speaking)$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return active practice tests grouped by skill; excludes mock-only entries."""
    groups: dict[str, list[dict[str, Any]]] = {
        "listening": [], "reading": [], "writing": [], "speaking": []
    }

    if skill in (None, "listening"):
        rows = (await db.execute(select(ListeningTest).where(
            ListeningTest.is_active.is_(True), ListeningTest.is_practice.is_(True)
        ).order_by(ListeningTest.created_at.desc()))).scalars().all()
        groups["listening"] = [
            {"id": x.id, "title": x.title, "skill": "listening",
             "cefr_level": x.cefr_level, "duration_minutes": x.duration_minutes,
             "total_questions": x.total_questions, "is_free": bool(x.is_free)} for x in rows
        ]

    if skill in (None, "reading"):
        rows = (await db.execute(select(ReadingTest).where(
            ReadingTest.is_active.is_(True), ReadingTest.is_practice.is_(True)
        ).order_by(ReadingTest.created_at.desc()))).scalars().all()
        groups["reading"] = [
            {"id": x.id, "title": x.title, "skill": "reading",
             "cefr_level": x.cefr_level, "duration_minutes": x.duration_minutes,
             "total_questions": x.total_questions, "is_free": bool(x.is_free)} for x in rows
        ]

    if skill in (None, "writing"):
        rows = (await db.execute(select(WritingExam).options(selectinload(WritingExam.tasks)).where(
            WritingExam.is_active.is_(True), WritingExam.is_practice.is_(True)
        ).order_by(WritingExam.created_at.desc()))).scalars().all()
        groups["writing"] = [
            {"id": x.id, "title": x.title, "skill": "writing",
             "cefr_level": getattr(x.cefr_level, "value", x.cefr_level),
             "duration_minutes": x.duration_minutes,
             "total_questions": len(x.tasks or []), "is_free": bool(x.is_free)} for x in rows
        ]

    if skill in (None, "speaking"):
        rows = (await db.execute(select(SpeakingTest).where(
            SpeakingTest.is_active.is_(True), SpeakingTest.is_practice.is_(True)
        ).order_by(SpeakingTest.created_at.desc()))).scalars().all()
        groups["speaking"] = [
            {"id": x.id, "title": x.title, "skill": "speaking",
             "cefr_level": None, "duration_minutes": None,
             "total_questions": None, "is_free": bool(x.is_free)} for x in rows
        ]

    return {"items": groups, "total": sum(map(len, groups.values()))}
