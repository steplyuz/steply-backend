from app.modules.exams.mock.usage import (
    ensure_deletable, purge_exam_versions, purge_practice_attempts, purge_results,
)
import logging
from app.modules.tests.scoring_policy import scale_listening_reading
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Mock Models (Integratsiya uchun)
import uuid
from app.modules.exams.mock.models import MockSkillAttempt, SkillType

# Reading Models & Schemas
from .models import (
    ReadingTest,
    ReadingPart,
    ReadingQuestion,
    ReadingOption,
    ReadingResult,
)
from .schemas import (
    ReadingTestCreate,
    ReadingTestUpdate,
    ReadingSubmitRequest,
    ReadingResultResponse,
    ReadingResultDetailResponse,
    ReadingQuestionReview,
)

from app.modules.exams.paper.models import PaperModule
from app.modules.exams.paper.schemas import LockValidationError
from app.modules.exams.paper.exam_paper_mixin import PaperVersionedExamMixin
from app.modules.exams.mock.finalize import try_finalize_mock_attempt
from app.core.id_generator import generate_reading_id
from app.core.clock import utcnow

logger = logging.getLogger(__name__)

class ReadingService(PaperVersionedExamMixin):
    # PaperVersionedExamMixin konfiguratsiyasi — paper/answer-key/answer-sheet
    # metodlari shu yerdan emas, umumiy mixindan keladi (Listening bilan
    # takrorlanmasligi uchun).
    PAPER_MODULE = PaperModule.READING
    MODULE_LABEL = "READING"
    NOT_FOUND_DETAIL = "Reading test topilmadi"
    TEMPLATE_PARTS = {1: (1, 6), 2: (7, 14), 3: (15, 20), 4: (21, 29), 5: (30, 35)}
    TEMPLATE_ORDERED = False
    _QUESTION_MODEL = ReadingQuestion
    _PART_MODEL = ReadingPart
    _PART_EXAM_FK = "test_id"
    USAGE_SKILL = "reading"
    RESULT_MODEL = ReadingResult
    RESULT_COL = ReadingResult.test_id

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load_exam_or_404(self, exam_id: str):
        test = await self.get_test_by_id(exam_id)
        if not test:
            raise HTTPException(status_code=404, detail=self.NOT_FOUND_DETAIL)
        return test

    async def lock_exam(self, test_id: str, locked_by_user_id: Optional[int] = None):
        test = await self._load_exam_or_404(test_id)
        partial = self._is_partial(test.parts, test.is_practice)
        try:
            self._validate_template_structure(test.parts, is_practice=test.is_practice)
        except HTTPException as exc:
            return None, [LockValidationError(message=str(exc.detail))]
        questions = await self._question_dicts_for_paper(test_id)
        return await self._paper_service(test_id).lock(questions, locked_by_user_id, partial=partial)

        # ================================================================
    #  PAPER PDF (module-specific)
    # ================================================================
    async def generate_paper_pdf(self, test_id: str) -> bytes:
        from app.modules.exams.paper.question_paper_pdf import render_reading_pdf

        test = await self.get_test_by_id(test_id)
        if not test:
            raise HTTPException(status_code=404, detail="Reading test topilmadi")
        await self.get_locked_version(test_id)

        parts_payload = []
        for order, part in enumerate(test.parts, start=1):
            questions_payload = [
                {
                    "question_number": q.question_number,
                    "type": q.type.value if hasattr(q.type, "value") else q.type,
                    "text": q.text,
                    "options": [{"label": o.label, "value": o.value} for o in q.options],
                }
                for q in sorted(part.questions, key=lambda q: q.question_number)
            ]
            parts_payload.append({
                "part_number": order,
                "title": part.title,
                "description": part.description,
                "passage": part.passage,
                "image_url": part.image_url,
                "questions": questions_payload,
            })

        exam_payload = {
            "title": test.title,
            "cefr_level": test.cefr_level,
            "language": getattr(test, "language", "en"),
            "duration_minutes": test.duration_minutes,
            "parts": parts_payload,
        }
        return render_reading_pdf(exam_payload)

    def _calculate_metrics(self, correct_count: int) -> Tuple[float, str]:
        """Raw → (ball, CEFR). Formula yagona manbada: scoring_policy.scale_listening_reading."""
        return scale_listening_reading(correct_count)

    # ================================================================
    #  2. TESTLARNI BOSHQARISH (CRUD)
    # ================================================================
    async def create_test(self, data: ReadingTestCreate) -> ReadingTest:
        # Partlar JSON'da qanday tartibda kelmasin — savol raqami bo'yicha 1..5 ga keltiriladi.
        data.parts = self.sort_parts_by_question(data.parts)
        # To'liq 5 part -> qat'iy shablon; part-only/practice -> yengil tekshiruv.
        if "is_practice" not in data.model_fields_set and self._is_partial(data.parts):
            data.is_practice = True  # JSON'da ko'rsatilmagan bo'lsa, part-only test practice hisoblanadi
        self._validate_template_structure(data.parts, is_practice=data.is_practice)
        # Auto-generate ID — client never supplies it
        exam_id = data.id or generate_reading_id()
        if data.id and await self.db.get(ReadingTest, exam_id):
            raise HTTPException(409, "Bu test ID allaqachon mavjud")

        # Compute total_questions from parts if not explicitly provided
        computed_total = sum(len(part.questions) for part in data.parts)
        total_questions = data.total_questions if data.total_questions is not None else computed_total

        test = ReadingTest(
            id=exam_id, title=data.title, cefr_level=data.cefr_level,
            language=data.language, duration_minutes=data.duration_minutes,
            total_questions=total_questions, is_demo=data.is_demo,
            is_free=data.is_free, is_practice=data.is_practice, is_active=data.is_active,
            parent_test_id=getattr(data, "parent_test_id", None),
            source_part_id=getattr(data, "source_part_id", None),
            practice_part_number=getattr(data, "practice_part_number", None),
        )

        for part_index, part_data in enumerate(data.parts, start=1):
            part = ReadingPart(
                external_id=part_data.external_id or str(uuid.uuid4()), title=part_data.title, description=part_data.description,
                passage=part_data.passage, image_url=part_data.image_url,
            )
            for q_index, q in enumerate(part_data.questions, start=1):
                question = ReadingQuestion(
                    external_id=q.external_id or str(uuid.uuid4()), question_number=q.question_number, type=q.type,
                    text=q.text, correct_answer=q.correct_answer,
                    word_limit=q.word_limit,
                )
                for opt_index, opt in enumerate(q.options or [], start=1):
                    question.options.append(ReadingOption(external_id=opt.external_id or str(uuid.uuid4()), label=opt.label, value=opt.value))
                part.questions.append(question)
            test.parts.append(part)

        self.db.add(test)
        await self.db.commit()
        return await self.get_test_by_id(test.id)


    async def create_part_mini_tests(self, test_id: str) -> dict:
        """Create one standalone practice test per part; repeated calls are idempotent."""
        source = await self.get_test_by_id(test_id)
        if not source:
            raise HTTPException(status_code=404, detail="Reading test topilmadi")
        if source.parent_test_id is not None:
            raise HTTPException(status_code=400, detail="Mini-testdan yana mini-test yaratib bo'lmaydi")
        if not source.parts:
            raise HTTPException(status_code=400, detail="Testda partlar mavjud emas")
        if any(not part.questions for part in source.parts):
            raise HTTPException(status_code=400, detail="Mini-test yaratish uchun har bir partda kamida bitta savol bo'lishi kerak")

        existing_rows = await self.db.execute(
            select(ReadingTest).where(ReadingTest.parent_test_id == source.id)
        )
        existing = {row.source_part_id: row for row in existing_rows.scalars().all()}
        created = []
        new_count = 0
        ordered_parts = sorted(source.parts, key=lambda item: item.id or 0)
        part_count = len(ordered_parts)
        per_part_minutes = 20  # Reading practice: har bir part 20 daqiqa

        for part_number, part in enumerate(ordered_parts, start=1):
            mini = existing.get(part.id)
            if mini is None:
                mini_id = generate_reading_id()
                mini = ReadingTest(
                    id=mini_id,
                    title=f"{source.title} — Part {part_number}: {part.title}",
                    cefr_level=source.cefr_level,
                    language=source.language or "en",
                    duration_minutes=per_part_minutes,
                    total_questions=len(part.questions),
                    is_demo=False,
                    is_free=bool(source.is_free),
                    is_practice=True,
                    is_active=bool(source.is_active),
                    parent_test_id=source.id,
                    source_part_id=part.id,
                    practice_part_number=part_number,
                )
                mini_part = ReadingPart(
                    external_id=str(uuid.uuid4()),
                    title=part.title,
                    description=part.description,
                    passage=part.passage,
                    image_url=part.image_url,
                )
                for q_index, q in enumerate(sorted(part.questions, key=lambda item: item.question_number), start=1):
                    mini_q = ReadingQuestion(
                        external_id=str(uuid.uuid4()),
                        question_number=q_index,
                        type=q.type,
                        text=q.text,
                        correct_answer=q.correct_answer,
                        word_limit=q.word_limit,
                    )
                    for opt_index, opt in enumerate(q.options or [], start=1):
                        mini_q.options.append(ReadingOption(
                            external_id=str(uuid.uuid4()), label=opt.label, value=opt.value
                        ))
                    mini_part.questions.append(mini_q)
                mini.parts.append(mini_part)
                self.db.add(mini)
                created.append(mini)
                new_count += 1
            else:
                if mini.duration_minutes != per_part_minutes:
                    mini.duration_minutes = per_part_minutes   # eski mini-testlar ham 20 daqiqaga o'tadi
                created.append(mini)

        await self.db.commit()
        ids = [item.id for item in created]
        stmt = (
            select(ReadingTest)
            .where(ReadingTest.id.in_(ids))
            .options(selectinload(ReadingTest.parts).selectinload(ReadingPart.questions).selectinload(ReadingQuestion.options))
            .order_by(ReadingTest.practice_part_number.asc())
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return {
            "source_test_id": source.id,
            "created_count": new_count,
            "total_count": len(rows),
            "tests": [{"id": row.id, "title": row.title, "practice_part_number": row.practice_part_number} for row in rows],
        }

    async def get_all_tests(
        self,
        *,
        is_active: Optional[bool] = None,
        is_practice: Optional[bool] = None,
        is_free: Optional[bool] = None,
        only_ids: Optional[List[str]] = None,
    ) -> List[ReadingTest]:
        # 'selectinload' ishlatilishi shart (MissingGreenlet xatosi uchun)
        stmt = (
            select(ReadingTest)
            .options(
                selectinload(ReadingTest.parts)
                .selectinload(ReadingPart.questions)
                .selectinload(ReadingQuestion.options)
            )
            .order_by(ReadingTest.created_at.desc())
        )
        if is_active is not None:
            stmt = stmt.where(ReadingTest.is_active.is_(is_active))
        if is_practice is not None:
            stmt = stmt.where(ReadingTest.is_practice.is_(is_practice))
        if is_free is not None:
            stmt = stmt.where(ReadingTest.is_free.is_(is_free))
        if only_ids is not None:
            stmt = stmt.where(ReadingTest.id.in_(only_ids))
        res = await self.db.execute(stmt)
        return res.scalars().all()

    async def get_test_by_id(self, test_id: str) -> Optional[ReadingTest]:
        stmt = (
            select(ReadingTest).where(ReadingTest.id == test_id)
            .options(
                selectinload(ReadingTest.parts)
                .selectinload(ReadingPart.questions)
                .selectinload(ReadingQuestion.options)
            )
        )
        res = await self.db.execute(stmt)
        return res.unique().scalar_one_or_none()

    async def update_test(self, test_id: str, data: ReadingTestUpdate) -> ReadingTest:
        locked = await self._paper_service(test_id).get_answer_key()
        if locked and locked.is_locked:
            raise HTTPException(409, "Reading test locked qilingan. Uni o'zgartirish mumkin emas.")
        test = await self.get_test_by_id(test_id)
        if not test: raise HTTPException(404, "Reading test topilmadi")

        if data.parts is not None:
            data.parts = self.sort_parts_by_question(data.parts)
            practice = data.is_practice if data.is_practice is not None else test.is_practice
            self._validate_template_structure(data.parts, is_practice=practice)

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field != "parts": setattr(test, field, value)

        if data.parts is not None:
            for old in list(test.parts):
                await self.db.delete(old)
            await self.db.flush()
            self.db.expire(test, ["parts"])
            for part_index, part_data in enumerate(data.parts, start=1):
                part = ReadingPart(
                    test_id=test_id, external_id=part_data.external_id or str(uuid.uuid4()), title=part_data.title,
                    description=part_data.description, passage=part_data.passage, image_url=part_data.image_url,
                )
                for q_index, q in enumerate(part_data.questions, start=1):
                    question = ReadingQuestion(
                        external_id=q.external_id or str(uuid.uuid4()), question_number=q.question_number, type=q.type,
                        text=q.text, correct_answer=q.correct_answer,
                        word_limit=q.word_limit,
                    )
                    for opt in q.options or []:
                        question.options.append(ReadingOption(external_id=opt.external_id or str(uuid.uuid4()), label=opt.label, value=opt.value))
                    part.questions.append(question)
                self.db.add(part)

        await self.db.commit()
        return await self.get_test_by_id(test_id)

    async def delete_test(self, test_id: str, force: bool = False) -> bool:
        """Testni o'chirish. Natija/urinish bo'lsa 409; force=True (faqat super admin) bilan hammasi o'chadi."""
        test = await self.db.get(ReadingTest, test_id)
        if not test:
            return False
        await ensure_deletable(
            self.db, skill=self.USAGE_SKILL, module=self.PAPER_MODULE, test_id=test_id,
            result_model=self.RESULT_MODEL, result_col=self.RESULT_COL, force=force,
        )
        await purge_results(self.db, self.RESULT_MODEL, self.RESULT_COL, test_id)
        await purge_practice_attempts(self.db, self.USAGE_SKILL, test_id)
        await purge_exam_versions(self.db, self.PAPER_MODULE, test_id)
        await self.db.delete(test)
        await self.db.commit()
        return True

    # ================================================================
    #  3. JAVOBLARNI TEKSHIRISH (SUBMIT)
    # ================================================================
    async def submit_answers(self, user_id: uuid.UUID, test_id: str, data: ReadingSubmitRequest) -> ReadingResultDetailResponse:
        """Javoblarni yuborish (eski kontrakt saqlangan).

        Body'dagi javoblar avval DB'ga upsert qilinadi, baholash esa SAQLANGAN
        javoblardan server tomonda bajariladi va natija DB'ga yoziladi
        (qarang: attempt_service). Body bo'sh bo'lsa — faqat oldindan
        saqlangan javoblar ishlatiladi. Takroriy yuborish mavjud natijani qaytaradi.
        """
        from app.modules.tests.attempts import common as at
        from app.modules.tests.attempts import submit as _sub

        test = await self.get_test_by_id(test_id)
        if not test: raise HTTPException(404, "Test topilmadi")
        questions = [q for part in test.parts for q in part.questions]
        numbers = {q.question_number for q in questions}
        incoming = {}
        for a in data.answers:
            if a.question_id is None and a.question_number not in numbers:
                continue  # eski xatti-harakat: noma'lum raqam e'tiborga olinmaydi
            q = at.resolve_question(questions, a.question_id, a.question_number)
            incoming[q.question_number] = at.normalize_answer(a.answers)

        result = await _sub.submit_with_answers(
            self.db, at.spec_for(SkillType.READING), user_id, test, data.exam_attempt_id, incoming
        )
        return await self.get_result_with_review(result.id, user_id)

    async def get_user_results(self, user_id: uuid.UUID) -> List[ReadingResult]:
        stmt = select(ReadingResult).where(ReadingResult.user_id == user_id).order_by(ReadingResult.created_at.desc())
        res = await self.db.execute(stmt)
        return res.scalars().all()

    async def get_result_with_review(self, result_id: int, user_id: uuid.UUID) -> Optional[ReadingResultDetailResponse]:
        stmt = select(ReadingResult).where(ReadingResult.id == result_id, ReadingResult.user_id == user_id)
        res = await self.db.execute(stmt)
        result = res.scalar_one_or_none()
        
        if not result: return None

        test = await self.get_test_by_id(result.test_id)
        if not test: return None

        review_items = []
        for part in test.parts:
            for q in sorted(part.questions, key=lambda item: item.question_number):
                u_ans = result.user_answers.get(str(q.question_number), []) or []
                c_ans = q.correct_answer

                def normalize_answers(value):
                    if value is None:
                        return set()
                    values = value if isinstance(value, (list, tuple, set)) else str(value).split("/")
                    return {str(item).strip().casefold() for item in values if str(item).strip()}

                correct_set = normalize_answers(c_ans)
                user_set = normalize_answers(u_ans)
                is_correct = bool(user_set) and user_set == correct_set
                correct_list = list(c_ans) if isinstance(c_ans, (list, tuple)) else [
                    item.strip() for item in str(c_ans or "").split("/") if item.strip()
                ]

                review_items.append(ReadingQuestionReview(
                    question_number=q.question_number,
                    question_text=q.text,
                    part_title=part.title,
                    user_answer=[str(value) for value in u_ans],
                    correct_answer=[str(value) for value in correct_list],
                    is_correct=is_correct,
                    type=q.type.value if hasattr(q.type, "value") else str(q.type),
                ))

        return ReadingResultDetailResponse(
            summary=ReadingResultResponse.model_validate(result),
            review=review_items
        )

    # ================================================================
    #  4. MOCK EXAM INTEGRATION
    # ================================================================
    async def _update_mock_reading(
        self, exam_attempt_id: int, score: float, cefr_level: str, user_id: uuid.UUID,
        raw_score: float = 0.0,
    ):
        # IDOR FIX: `exam_attempt_id` is client-supplied (ReadingSubmitRequest).
        # Without the `user_id` filter below, any authenticated user could pass
        # someone else's attempt_id here and overwrite that OTHER student's
        # Reading skill score / is_checked / submitted_at in their in-progress
        # (or already-finished) mock exam attempt, and could even trigger
        # _try_finish_mock_exam() to finalize the victim's attempt with a
        # score the attacker chose. MockSkillAttempt.user_id is set from the
        # real attempt owner at creation time (see
        # mock_test.services._create_attempt_with_skills), so filtering on it
        # here means this can only ever touch the CALLER's own skill row —
        # a mismatched exam_attempt_id now behaves exactly like "not found"
        # (silently skipped), matching the existing `if skill:` no-op path.
        stmt = select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == exam_attempt_id,
            MockSkillAttempt.skill == SkillType.READING,
            MockSkillAttempt.user_id == user_id,
        )
        skill = (await self.db.execute(stmt)).scalar_one_or_none()
        if skill:
            from app.modules.exams.mock.models import MockExamAttempt
            attempt = await self.db.get(MockExamAttempt, exam_attempt_id)
            if attempt and attempt.is_finished:
                raise HTTPException(409, "Yakunlangan imtihon natijasini o'zgartirib bo'lmaydi")
            # FIX: MockSkillAttempt'da `score` ustuni yo'q edi — ball jimgina yo'qolardi.
            skill.raw_score = float(raw_score)
            skill.scaled_score = float(score)
            skill.cefr_level = cefr_level
            skill.is_checked = True
            # FIX: ustun timezone'siz (DateTime) — tz-aware qiymat Postgres'da DataError beradi.
            skill.submitted_at = utcnow()
            await self.db.flush()
            await try_finalize_mock_attempt(self.db, exam_attempt_id)
