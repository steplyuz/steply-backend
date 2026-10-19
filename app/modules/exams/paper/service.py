"""
paper/service.py
=================
Listening va Reading uchun umumiy answer-key / exam-versioning logikasi
(Band 8, 15, 16, 21 dagi talablar — Faza 1).

Ishlatilishi (listening yoki reading service/router ichida):

    from app.modules.exams.paper.service import PaperVersionService

    rows = await db.execute(select(ListeningQuestion).join(ListeningPart)....)
    q_list = [
        {"question_number": q.question_number, "correct_answer": q.correct_answer,
         "type": q.type.value, "options_count": len(q.options)}
        for q in rows.scalars()
    ]
    svc = PaperVersionService(db, module=PaperModule.LISTENING, exam_id=exam_id)
    draft = await svc.sync_draft(q_list)          # joriy savollarga mos draft versiyani yangilaydi
    result = await svc.lock(q_list)                # validatsiya + lock

Bu servis atayin `ListeningQuestion`/`ReadingQuestion` modellariga bog'liq
emas — chaqiruvchi tomon savollarni oddiy dict ro'yxati sifatida uzatadi.
Shu tufayli ikkala modulda ham qayta yoziladi.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ExamVersion, PaperModule
from .schemas import LockValidationError


class PaperVersionService:
    def __init__(self, db: AsyncSession, module: PaperModule, exam_id: str):
        self.db = db
        self.module = module
        self.exam_id = exam_id

    # ------------------------------------------------------------------ #
    # VALIDATION (Band 21)
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate_questions(questions: List[Dict[str, Any]], partial: bool = False) -> List[LockValidationError]:
        errors: List[LockValidationError] = []

        if not questions:
            errors.append(LockValidationError(message="Exam bo'sh — hech qanday savol topilmadi."))
            return errors

        numbers = [q["question_number"] for q in questions]

        # Raqam yo'q / noto'g'ri
        for q in questions:
            if not q.get("question_number") or q["question_number"] <= 0:
                errors.append(LockValidationError(message="Savolda question_number ko'rsatilmagan."))

        # Dublikat raqamlar
        seen = set()
        for n in numbers:
            if n in seen:
                errors.append(LockValidationError(question_number=n, message=f"Savol raqami {n} takrorlangan."))
            seen.add(n)

        # Uzluksizlik (1..N)
        # partial (part-only) testda raqamlar istalgan joydan boshlanishi mumkin (masalan 15-20).
        start = min(numbers) if (partial and numbers) else 1
        expected = set(range(start, start + len(numbers)))
        missing = expected - set(numbers)
        for n in sorted(missing):
            errors.append(LockValidationError(question_number=n, message=f"Savol raqami {n} yetishmayapti."))

        # Har bir savolda correct_answer bo'lishi shart
        for q in questions:
            ans = q.get("correct_answer")
            if ans is None or ans == "" or ans == []:
                errors.append(
                    LockValidationError(
                        question_number=q.get("question_number"),
                        message=f"Savol {q.get('question_number')} uchun to'g'ri javob (answer key) kiritilmagan.",
                    )
                )

        # MULTIPLE_CHOICE kabi variant talab qiladigan turlar uchun kamida 2 variant
        needs_options = {"MULTIPLE_CHOICE", "MATCHING", "HEADINGS_MATCH", "TEXT_MATCH", "MULTIPLE_SELECT"}
        for q in questions:
            if q.get("type") in needs_options and q.get("options_count", 0) < 2:
                errors.append(
                    LockValidationError(
                        question_number=q.get("question_number"),
                        message=f"Savol {q.get('question_number')} ({q.get('type')}) uchun javob variantlari yetarli emas.",
                    )
                )

        return errors

    # ------------------------------------------------------------------ #
    # VERSIONS
    # ------------------------------------------------------------------ #
    async def _latest_version(self) -> Optional[ExamVersion]:
        res = await self.db.execute(
            select(ExamVersion)
            .where(ExamVersion.module == self.module, ExamVersion.exam_id == self.exam_id)
            .order_by(desc(ExamVersion.version_number))
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def list_versions(self) -> List[ExamVersion]:
        res = await self.db.execute(
            select(ExamVersion)
            .where(ExamVersion.module == self.module, ExamVersion.exam_id == self.exam_id)
            .order_by(desc(ExamVersion.version_number))
        )
        return list(res.scalars().all())

    async def get_answer_key(self, version_number: Optional[int] = None) -> Optional[ExamVersion]:
        if version_number is None:
            return await self._latest_version()
        res = await self.db.execute(
            select(ExamVersion).where(
                ExamVersion.module == self.module,
                ExamVersion.exam_id == self.exam_id,
                ExamVersion.version_number == version_number,
            )
        )
        return res.scalar_one_or_none()

    def _build_answer_key(self, questions: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {str(q["question_number"]): q.get("correct_answer") for q in questions}

    async def sync_draft(self, questions: List[Dict[str, Any]]) -> ExamVersion:
        """
        Joriy savol/javob to'plamiga mos DRAFT (lock qilinmagan) versiyani
        qaytaradi. Agar oxirgi versiya lock qilingan bo'lsa yoki mavjud
        bo'lmasa, yangi draft versiya yaratiladi. Agar oxirgi versiya draft
        bo'lsa, uni joriy savollarga mos yangilaydi (overwrite).
        """
        latest = await self._latest_version()
        answer_key = self._build_answer_key(questions)

        if latest is not None and not latest.is_locked:
            latest.answer_key = answer_key
            latest.total_questions = len(questions)
            await self.db.flush()
            return latest

        new_version_number = (latest.version_number + 1) if latest else 1
        version = ExamVersion(
            module=self.module,
            exam_id=self.exam_id,
            version_number=new_version_number,
            total_questions=len(questions),
            answer_key=answer_key,
            is_locked=False,
        )
        self.db.add(version)
        await self.db.flush()
        return version

    async def lock(self, questions: List[Dict[str, Any]], locked_by_user_id: Optional[int] = None, partial: bool = False):
        """
        Band 21: validatsiyadan o'tmasa lock qilinmaydi (demak PDF/OMR ham
        generatsiya qilinmaydi — chunki PDF/OMR generator faqat locked
        versiyadan foydalanadi).
        """
        errors = self.validate_questions(questions, partial=partial)
        if errors:
            return None, errors

        version = await self.sync_draft(questions)
        version.is_locked = True
        version.locked_at = datetime.now(timezone.utc)
        version.locked_by_user_id = locked_by_user_id
        await self.db.flush()
        return version, []
