from app.modules.exams.mock.usage import ensure_not_in_mock_exam, purge_exam_versions
import logging
import secrets
from typing import List

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from app.modules.exams.paper.content_lock import ContentLockService
from app.modules.exams.paper.models import PaperModule
from app.modules.exams.paper.schemas import LockValidationError

from .scoring import structure_errors, missing_full_structure, expected_format, format_mismatch
from ..models import (
    WritingExam,
    WritingFormat,
    WritingTask,
    WritingResult,
    CEFRLevel, WritingStyle, ScoringMode,
)
from ..schemas import WritingExamCreate, WritingExamUpdate
from app.core.clock import utcnow

logger = logging.getLogger(__name__)


class WritingAdminService:

    MAX_ID_RETRY = 5

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==========================================================
    # INTERNAL
    # ==========================================================

    async def _generate_exam_identity(self) -> tuple[str, int, int]:
        current_year = utcnow().year

        result = await self.db.execute(
            select(func.max(WritingExam.sequence_number))
            .where(WritingExam.year == current_year)
        )

        last_sequence = result.scalar() or 0
        new_sequence = last_sequence + 1

        exam_id = f"writing-exams-{current_year}-{new_sequence:03d}-{secrets.token_hex(4)}"

        return exam_id, current_year, new_sequence

    async def _validate_formats(self, tasks):
        """Har bir topshiriq uchun 2025 talabiga mos format ta'minlaydi.

        - format_id berilgan va mavjud bo'lsa: CEFR/uslub 2025 talabiga mos bo'lishi shart, aks holda 422.
        - format_id yo'q yoki mavjud emas: topshiriq qismiga (1.1/1.2/2) mos format avtomatik yaratiladi.
        """
        for t in tasks:
            part = int(t.part_number)
            if part in (1, 2) and (part == 2 or t.sub_part in (1, 2)):
                exp = expected_format(part, t.sub_part)
            else:
                exp = None  # structure_errors lock vaqtida xatoni ko'rsatadi
            if t.format_id is not None:
                found = await self.db.get(WritingFormat, t.format_id)
                if found:
                    if exp is not None:
                        msg = format_mismatch(part, t.sub_part, found.cefr_level.value, found.style.value)
                        if msg:
                            raise HTTPException(422, msg)
                    continue
            wl = t.word_limit
            if isinstance(wl, int):
                min_words, max_words = 1, max(1, wl)
            elif wl is not None:
                min_words, max_words = wl.min, wl.max
            elif exp is not None:
                min_words, max_words = exp["min_words"], exp["max_words"]
            else:
                min_words, max_words = 1, 1000
            if min_words == max_words:
                min_words = max(1, max_words - 1)
            if exp is not None:
                level_name, style_name = exp["cefr_level"], exp["style"]
            else:
                level_name = (t.cefr_level.value if t.cefr_level else "B1")
                style_name = "POST" if part == 2 else "FORMAL"
            fmt = WritingFormat(
                name=f"Auto {level_name} {part}.{t.sub_part or 1}-{secrets.token_hex(4)}",
                cefr_level=CEFRLevel(level_name), min_words=min_words, max_words=max_words,
                style=WritingStyle(style_name), scoring_mode=ScoringMode.HOLISTIC, penalty_enabled=False,
            )
            self.db.add(fmt)
            await self.db.flush()
            t.format_id = fmt.id

    async def _has_results(self, exam_id: str) -> bool:
        result = await self.db.execute(
            select(func.count())
            .select_from(WritingResult)
            .where(WritingResult.exam_id == exam_id)
        )
        return result.scalar_one() > 0

    async def _get_exam_or_404(self, exam_id: str) -> WritingExam:
        result = await self.db.execute(
            select(WritingExam)
            .where(WritingExam.id == exam_id)
        )
        exam = result.scalar_one_or_none()

        if not exam:
            raise HTTPException(404, "Exam not found")

        return exam

    # ==========================================================
    # CREATE
    # ==========================================================

    async def create_exam(self, data: WritingExamCreate) -> WritingExam:

        await self._validate_formats(data.tasks)

        for attempt in range(self.MAX_ID_RETRY):
            # ID is always server-generated: imported IDs must not be able to
            # collide with an existing exam or overwrite a previous record.
            # Sequence uniqueness is enforced separately by the DB constraint.
            exam_id, year, sequence_number = await self._generate_exam_identity()

            exam = WritingExam(
                id=exam_id,
                year=year,
                sequence_number=sequence_number,
                title=data.title,
                cefr_level=data.cefr_level,
                duration_minutes=data.duration_minutes,
                is_demo=data.is_demo,
                is_free=data.is_free,
                is_practice=data.is_practice,
                is_active=data.is_active,
            )

            self.db.add(exam)

            try:
                await self.db.flush()
                break
            except IntegrityError:
                await self.db.rollback()
                # The rollback also removes any auto-created rubric formats.
                # Recreate/validate them before retrying the exam insert.
                await self._validate_formats(data.tasks)
                logger.warning("Writing exam identity conflict; retry %s", attempt + 1)
        else:
            raise HTTPException(
                500,
                "Failed to generate unique exam ID"
            )

        tasks = [
            WritingTask(
                exam_id=exam.id,
                external_id=t.external_id or f"task-{i}",
                part_number=t.part_number,
                sub_part=t.sub_part,
                topic=t.topic,
                instruction=t.instruction,
                context_text=t.context_text,
                question=t.question,
                word_limit_min=(t.word_limit if isinstance(t.word_limit, int) else (t.word_limit.min if t.word_limit else None)),
                word_limit_max=(t.word_limit if isinstance(t.word_limit, int) else (t.word_limit.max if t.word_limit else None)),
                format_id=t.format_id,
            )
            for i, t in enumerate(data.tasks, start=1)
        ]

        self.db.add_all(tasks)

        await self.db.flush()
        await self.db.commit()

        return await self.get_exam_by_id(exam.id)

    # ==========================================================
    # READ
    # ==========================================================

    async def get_exam_by_id(self, exam_id: str) -> WritingExam:

        stmt = (
            select(WritingExam)
            .where(WritingExam.id == exam_id)
            .options(
                selectinload(WritingExam.tasks)
                .selectinload(WritingTask.format)
            )
            # Yangilashdan keyin sessiyadagi yangi task obyektlari format'siz qolmasin
            .execution_options(populate_existing=True)
        )

        result = await self.db.execute(stmt)
        exam = result.scalar_one_or_none()

        if not exam:
            raise HTTPException(404, "Exam not found")

        return exam

    async def list_exams(
        self,
        active_only: bool = False
    ) -> List[WritingExam]:

        stmt = select(WritingExam).options(
            selectinload(WritingExam.tasks)
            .selectinload(WritingTask.format)
        )

        if active_only:
            stmt = stmt.where(WritingExam.is_active.is_(True))

        result = await self.db.execute(stmt)

        return list(result.scalars().unique().all())

    # ==========================================================
    # UPDATE
    # ==========================================================

    async def update_exam(
        self,
        exam_id: str,
        data: WritingExamUpdate
    ) -> WritingExam:

        await ContentLockService(self.db, PaperModule.WRITING, exam_id).assert_unlocked("Writing test")
        if await self._has_results(exam_id):
            raise HTTPException(
                400,
                "Cannot update exam with existing submissions"
            )

        exam = await self._get_exam_or_404(exam_id)

        update_data = data.model_dump(
            exclude_unset=True,
            exclude={"tasks"}
        )

        for field, value in update_data.items():
            setattr(exam, field, value)

        if data.tasks is not None:

            await self._validate_formats(data.tasks)

            old_tasks = (await self.db.execute(
                select(WritingTask).where(WritingTask.exam_id == exam_id)
            )).scalars().all()
            for old in old_tasks:
                await self.db.delete(old)
            await self.db.flush()

            new_tasks = [
                WritingTask(
                    exam_id=exam.id,
                    external_id=t.external_id or f"task-{i}",
                    part_number=t.part_number,
                    sub_part=t.sub_part,
                    topic=t.topic,
                    instruction=t.instruction,
                    context_text=t.context_text,
                    question=t.question,
                    word_limit_min=(t.word_limit if isinstance(t.word_limit, int) else (t.word_limit.min if t.word_limit else None)),
                    word_limit_max=(t.word_limit if isinstance(t.word_limit, int) else (t.word_limit.max if t.word_limit else None)),
                    format_id=t.format_id,
                )
                for i, t in enumerate(data.tasks, start=1)
            ]

            self.db.add_all(new_tasks)

        await self.db.flush()
        await self.db.commit()

        return await self.get_exam_by_id(exam_id)

    # ==========================================================
    # DELETE
    # ==========================================================

    async def delete_exam(self, exam_id: str):

        if await self._has_results(exam_id):
            raise HTTPException(
                409,
                "Bu testda topshirilgan natijalar bor, o'chirib bo'lmaydi. Uni faolsizlantiring (is_active=false).",
            )
        await ensure_not_in_mock_exam(self.db, "writing", exam_id)
        await purge_exam_versions(self.db, PaperModule.WRITING, exam_id)

        exam = await self._get_exam_or_404(exam_id)
        await self.db.delete(exam)

        await self.db.flush()
        await self.db.commit()

        return {"status": "deleted"}

    # ==========================================================
    # LOCK
    # ==========================================================

    async def lock_exam(self, exam_id: str, locked_by_user_id=None):
        """Kontent tuzilmasini tekshiradi va muzlatadi. To'liq test: Part 1 va Part 2 majburiy;
        practice/part-only test: kamida bitta to'liq task yetarli."""
        exam = await self.get_exam_by_id(exam_id)
        errors: list[LockValidationError] = []
        tasks = list(exam.tasks or [])
        if not tasks:
            errors.append(LockValidationError(message="Testda hech qanday writing task yo'q."))
        for t in tasks:
            if not (t.topic or "").strip() or not (t.instruction or "").strip():
                errors.append(LockValidationError(
                    message=f"Part {t.part_number}: topic va instruction to'ldirilishi shart."))
            if t.word_limit_min and t.word_limit_max and t.word_limit_min > t.word_limit_max:
                errors.append(LockValidationError(
                    message=f"Part {t.part_number}: word_limit min max'dan katta."))
        # 2025 tuzilmasi: Part 1 → sub_part 1 (1.1) va 2 (1.2), Part 2 → bitta topshiriq.
        for message in structure_errors(tasks):
            errors.append(LockValidationError(message=message))
        if not exam.is_practice:
            missing = missing_full_structure(tasks)
            if missing:
                errors.append(LockValidationError(
                    message=f"To'liq Writing testda topshiriqlar yetishmayapti: {missing} "
                            "(qismlarga ajratilgan test uchun is_practice=true qiling)."))
        snapshot = {"tasks": [
            {"part_number": t.part_number, "sub_part": t.sub_part, "topic": t.topic,
             "word_limit_min": t.word_limit_min, "word_limit_max": t.word_limit_max}
            for t in sorted(tasks, key=lambda x: (x.part_number, x.sub_part or 0))
        ]}
        return await ContentLockService(self.db, PaperModule.WRITING, exam_id).lock(
            snapshot, len(tasks), errors, locked_by_user_id)