"""Paper PDF facade.

Paper PDFs use the approved master templates when available. If a template is
missing or fixed slots cannot fit the actual exam content, a clearly labelled,
readable fallback PDF is generated from the same source data.
"""
from __future__ import annotations

import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.modules.exams.paper.question_paper_pdf import (
    render_listening_pdf, render_reading_pdf, render_writing_pdf,
)


def _qdict(q):
    return {
        "question_number": q.question_number,
        "type": q.type.value if hasattr(q.type, "value") else str(q.type),
        "text": q.text or "",
        "options": [{"label": o.label, "value": o.value} for o in getattr(q, "options", [])],
    }


class ListeningPaperPDF:
    async def generate(self, exam_id: str, db: AsyncSession) -> bytes:
        from app.modules.tests.listening.models import ListeningTest, ListeningPart
        stmt = select(ListeningTest).where(ListeningTest.id == exam_id).options(
            selectinload(ListeningTest.parts).selectinload(ListeningPart.questions)
        )
        exam = (await db.execute(stmt)).unique().scalar_one_or_none()
        if not exam:
            raise ValueError(f"Listening exam topilmadi: {exam_id}")
        payload = {"title": exam.title, "cefr_level": exam.cefr_level, "duration_minutes": exam.duration_minutes, "parts": []}
        for p in sorted(exam.parts, key=lambda x: x.part_number):
            payload["parts"].append({
                "part_number": p.part_number, "title": p.title, "instruction": p.instruction,
                "task_type": p.task_type, "audio_url": p.audio_url, "context": p.context,
                "passage": p.passage, "map_image": p.map_image,
                "options": [{"label": o.label, "value": o.value} for o in getattr(p, "options", [])],
                "questions": [_qdict(q) for q in sorted(p.questions, key=lambda x: x.question_number)],
            })
        return await asyncio.to_thread(render_listening_pdf, payload)


class ReadingPaperPDF:
    async def generate(self, test_id: str, db: AsyncSession) -> bytes:
        from app.modules.tests.reading.models import ReadingTest, ReadingPart
        stmt = select(ReadingTest).where(ReadingTest.id == test_id).options(
            selectinload(ReadingTest.parts).selectinload(ReadingPart.questions).selectinload(__import__("app.modules.tests.reading.models", fromlist=["ReadingQuestion"]).ReadingQuestion.options)
        )
        test = (await db.execute(stmt)).unique().scalar_one_or_none()
        if not test:
            raise ValueError(f"Reading test topilmadi: {test_id}")
        payload = {"title": test.title, "cefr_level": test.cefr_level, "language": test.language, "duration_minutes": test.duration_minutes, "parts": []}
        for i, p in enumerate(sorted(test.parts, key=lambda x: x.id), 1):
            payload["parts"].append({
                "part_number": i, "title": p.title, "description": p.description, "passage": p.passage,
                "questions": [_qdict(q) for q in sorted(p.questions, key=lambda x: x.question_number)],
            })
        return await asyncio.to_thread(render_reading_pdf, payload)


class WritingPaperPDF:
    async def generate(self, exam_id: str, db: AsyncSession) -> bytes:
        from app.modules.tests.writing.models import WritingExam, WritingTask
        stmt = select(WritingExam).where(WritingExam.id == exam_id).options(
            selectinload(WritingExam.tasks).selectinload(WritingTask.format)
        )
        exam = (await db.execute(stmt)).unique().scalar_one_or_none()
        if not exam:
            raise ValueError(f"Writing exam topilmadi: {exam_id}")
        payload = {"title": exam.title, "cefr_level": getattr(exam.cefr_level, "value", exam.cefr_level), "duration_minutes": exam.duration_minutes, "tasks": []}
        for t in sorted(exam.tasks, key=lambda x: (x.part_number, x.sub_part or 0, x.id)):
            fmt = t.format
            payload["tasks"].append({
                "id": t.id, "part_number": t.part_number, "sub_part": t.sub_part,
                "topic": t.topic, "instruction": t.instruction, "context_text": t.context_text,
                "format": {"min_words": fmt.min_words, "max_words": fmt.max_words} if fmt else None,
            })
        return await asyncio.to_thread(render_writing_pdf, payload)
