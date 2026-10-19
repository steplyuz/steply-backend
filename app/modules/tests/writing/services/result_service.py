import asyncio
import json
import re
from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.modules.auth.models import User

import uuid
from ..models import (
    WritingExam,
    WritingResult,
    WritingAnswer,
)
from app.modules.exams.mock.models import PracticeAttempt, PracticeAttemptStatus, SkillType
from ..schemas import WritingSubmitRequest
from app.core.clock import utcnow
from .scoring import count_words, expert_to_scaled, level_from_scaled, task_spec


# Bir so'rovda barcha AI chaqiruvlari uchun umumiy chegara (sekund).
# Har bir chaqiruv 30s timeout va 2 urinish bilan ishlaydi; chegara frontend kutish
# vaqtidan oshmasligi uchun qat'iy qo'yilgan.
AI_TOTAL_TIMEOUT_SECONDS = 100

_WORD_RE = re.compile(r"\w+")


def _task_text(task) -> str:
    return "\n".join(x for x in [task.topic, task.instruction, task.context_text, task.question] if x)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def is_task_copy(answer: str, exam_task_text: str, n: int = 5, threshold: float = 0.5) -> bool:
    """Javob topshiriq matnidan nusxa ko'chirilganmi yoki yo'qmi.

    So'zlar bo'yicha o'xshashlik umumiy so'zlar (the, to, library ...) tufayli haqiqiy
    javoblarni ham nusxa deb topishi mumkin. Shuning uchun n so'zli ketma-ketliklar
    (n-gram) solishtiriladi: haqiqiy javob topshiriqdagi ketma-ketliklarni deyarli
    takrorlamaydi, nusxa esa ularning ko'pini takrorlaydi.
    """
    answer_tokens = _tokens(answer)
    if len(answer_tokens) < n:
        return False
    answer_grams = _ngrams(answer_tokens, n)
    task_grams = _ngrams(_tokens(exam_task_text), n)
    if not answer_grams or not task_grams:
        return False
    shared = len(answer_grams & task_grams) / len(answer_grams)
    return shared >= threshold


def build_exam_task_text(tasks) -> str:
    """Imtihondagi barcha topshiriq matnlarini bitta satrga yig'adi."""
    parts: list[str] = []
    for task in tasks or []:
        for value in (task.topic, task.instruction, task.context_text, task.question):
            if value:
                parts.append(value)
    return "\n".join(parts)


class WritingResultService:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================================================
    # PRACTICE SUBMISSION (inson tomonidan qo'lda baholanadi)
    # ==================================================

    async def submit_practice_answers(
        self, user_id: uuid.UUID, exam_id: str, data: WritingSubmitRequest
    ) -> WritingResult:
        """Writing javoblarini saqlaydi. AI baholash submit route tomonidan
        kvota tekshirilgach `ai_grade_result()` orqali bajariladi."""
        exam = await self.db.scalar(
            select(WritingExam)
            .where(WritingExam.id == exam_id, WritingExam.is_active.is_(True))
            .options(selectinload(WritingExam.tasks))
        )
        if not exam:
            raise HTTPException(404, "Writing exam topilmadi yoki faol emas")

        from app.modules.billing.entitlements import enforce_practice_limit, owns_mock_attempt
        is_mock_attempt = data.exam_attempt_id is not None and await owns_mock_attempt(self.db, user_id, data.exam_attempt_id)
        if data.exam_attempt_id is not None and not is_mock_attempt:
            # Legacy contract: an invalid/foreign mock attempt must never be used
            # to attach data to another user's mock exam; it falls back to a normal
            # practice submission and is still quota-checked server-side.
            data.exam_attempt_id = None

        if data.part_number is not None:
            allowed_task_ids = {t.id for t in exam.tasks if t.part_number == data.part_number}
            if not allowed_task_ids:
                raise HTTPException(404, "Writing practice qismi topilmadi")
        else:
            allowed_task_ids = {t.id for t in exam.tasks}

        if data.exam_attempt_id is not None:
            existing = await self.db.scalar(select(WritingResult.id).where(
                WritingResult.exam_id == exam_id,
                WritingResult.exam_attempt_id == data.exam_attempt_id,
                WritingResult.user_id == user_id,
            ).limit(1))
            if existing is not None:
                raise HTTPException(409, "Bu Writing skill allaqachon topshirilgan")

        task_map = {t.id: t for t in exam.tasks if t.id in allowed_task_ids}
        for a in data.answers:
            if a.task_id not in task_map:
                raise HTTPException(400, f"Task {a.task_id} bu exam'ga tegishli emas")

        practice_attempt = None
        if data.exam_attempt_id is None:
            practice_attempt = PracticeAttempt(
                user_id=user_id, skill=SkillType.WRITING, test_id=exam_id,
                mode="PART_PRACTICE" if data.part_number is not None else "FULL_PRACTICE",
                part_number=data.part_number, status=PracticeAttemptStatus.IN_PROGRESS,
            )
            self.db.add(practice_attempt)
            await self.db.flush()
            await enforce_practice_limit(
                self.db, user_id, "WRITING", exam_id,
                mode=practice_attempt.mode, attempt_id=practice_attempt.id, test=exam,
            )

        result = WritingResult(
            user_id=user_id, exam_id=exam_id, exam_attempt_id=data.exam_attempt_id,
            practice_attempt_id=practice_attempt.id if practice_attempt else None,
        )
        self.db.add(result)
        await self.db.flush()

        for a in data.answers:
            content = a.content.strip()
            self.db.add(
                WritingAnswer(
                    result_id=result.id,
                    task_id=a.task_id,
                    content=content,
                    word_count=len(content.split()),
                )
            )

        if data.exam_attempt_id is not None:
            await self._link_to_mock_attempt(user_id, data.exam_attempt_id, data.answers)
        elif practice_attempt is not None:
            practice_attempt.status = PracticeAttemptStatus.SUBMITTED
            practice_attempt.submitted_at = datetime.now(timezone.utc)
            practice_attempt.result_id = result.id

        await self.db.commit()
        return await self.get_result_by_id(result.id)

    async def _link_to_mock_attempt(self, user_id: uuid.UUID, exam_attempt_id: int, answers) -> None:
        """Mock imtihon urinishiga bog'lash — FAQAT matnni saqlash uchun
        (admin keyin mvp_router.score_skill orqali baholaydi). `is_checked`
        BU YERDA o'zgartirilmaydi, chunki hali hech kim baholamagan.

        IDOR FIX: reading/listening/speaking'dagi tasdiqlangan patternga mos
        — `user_id` filtri bilan, boshqa foydalanuvchining attempt_id'siga
        yozib qo'yish mumkin emas (mos kelmasa jimgina hech narsa qilinmaydi).
        """
        from app.modules.exams.mock.models import MockSkillAttempt, SkillType

        stmt = select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == exam_attempt_id,
            MockSkillAttempt.skill == SkillType.WRITING,
            MockSkillAttempt.user_id == user_id,
        )
        skill = (await self.db.execute(stmt)).scalar_one_or_none()
        if skill:
            skill.user_answers = {str(a.task_id): a.content.strip() for a in answers}

    # ==================================================
    # ADMIN: QO'LDA BAHOLASH
    # ==================================================

    async def list_pending(self) -> list[WritingResult]:
        """Return every unfinalized Writing submission to authorized evaluators.

        Access to submit practice work is enforced at submission time. The
        evaluator queue must not hide already-submitted work based on the
        candidate's current subscription tier, otherwise teachers cannot
        process valid pending submissions after a plan changes or expires.
        """
        stmt = select(WritingResult).where(
            or_(
                WritingResult.evaluation_status == "TEACHER_PENDING",
                WritingResult.evaluation_status == "PENDING",
            ),
        )
        result = await self.db.execute(
            stmt
            .options(selectinload(WritingResult.answers), selectinload(WritingResult.user).selectinload(User.profile), selectinload(WritingResult.user).selectinload(User.contacts))
            .order_by(WritingResult.created_at.asc())
        )
        return list(result.scalars().unique().all())

    async def grade_result(
        self, result_id: int, raw_score: float, cefr_level, feedback: str | None,
        criterion_scores: list[tuple[object, float]] | None = None, evaluator_id: uuid.UUID | None = None,
        answer_id: int | None = None,
    ) -> WritingResult:
        from ..models import FeedbackSource, WritingFeedback

        result = await self.get_result_by_id(result_id)
        target_answer = None
        if answer_id is not None:
            target_answer = next((a for a in result.answers if a.id == answer_id), None)
            if target_answer is None:
                raise HTTPException(404, "Writing answer topilmadi")
        elif result.answers:
            target_answer = result.answers[0]
        if result.exam_attempt_id:
            from app.modules.exams.mock.models import MockExamAttempt
            attempt = await self.db.get(MockExamAttempt, result.exam_attempt_id)
            if attempt and attempt.is_finished:
                raise HTTPException(409, "Yakunlangan imtihon Writing natijasini o'zgartirib bo'lmaydi")
        final = expert_to_scaled(raw_score)  # ekspert yig‘indisi 0–16 → 0–75 yakuniy
        result.raw_score = raw_score
        result.scaled_score = float(final)
        result.cefr_level = cefr_level
        result.is_finalized = True
        result.evaluation_status = "HUMAN_FINAL"
        result.human_evaluator_id = evaluator_id

        if target_answer is not None:
            if criterion_scores:
                from ..models import WritingScore
                for criterion, score in criterion_scores:
                    existing = next((x for x in target_answer.scores if x.criterion == criterion), None)
                    if existing:
                        existing.score = score
                    else:
                        self.db.add(WritingScore(answer_id=target_answer.id, criterion=criterion, score=score))

            if feedback:
                self.db.add(
                    WritingFeedback(
                        answer_id=target_answer.id,
                        source=FeedbackSource.HUMAN,
                        content=feedback,
                    )
                )

        # Mock imtihon bo'lsa — baho MockSkillAttempt'ga ham yozilsin va
        # 4 skill tayyor bo'lsa umumiy natija yaratilsin.
        if result.exam_attempt_id:
            from app.modules.exams.mock.models import MockSkillAttempt, SkillType
            from app.modules.exams.mock.finalize import overall_cefr_level, try_finalize_mock_attempt

            skill_row = (await self.db.execute(
                select(MockSkillAttempt).where(
                    MockSkillAttempt.attempt_id == result.exam_attempt_id,
                    MockSkillAttempt.skill == SkillType.WRITING,
                )
            )).scalar_one_or_none()
            if skill_row:
                skill_row.raw_score = float(raw_score)
                skill_row.scaled_score = float(final)
                skill_row.cefr_level = overall_cefr_level(float(final))
                skill_row.is_checked = True
                skill_row.submitted_at = skill_row.submitted_at or utcnow()
                await self.db.flush()
                await try_finalize_mock_attempt(self.db, result.exam_attempt_id)

        await self.db.commit()
        return await self.get_result_by_id(result_id)

    async def ai_grade_result(
        self,
        result_id: int,
        answer_id: int | None = None,
        purchased_answer_ids: set[int] | None = None,
        record_usage: bool = True,
        finalize: bool = True,
    ) -> WritingResult:
        """AI bilan baholash (2025 format): 1.1 → 0–5, 1.2 → 0–5, 2 → 0–6; jami 0–16 → 0–75.

        1) reja (DB'ga tegmaydi); 2) AI chaqiruvlari parallel; 3) natijalar ketma-ket yoziladi.
        record_usage=False bo‘lsa (admin/teacher) talabaning AI kvotasi hisobdan chiqarilmaydi.
        """
        from ..models import FeedbackSource
        from .ai_grader import WritingAIGrader

        purchased_answer_ids = purchased_answer_ids or set()
        result = await self.get_result_by_id(result_id)
        if result.evaluation_status == "HUMAN_FINAL":
            return result  # o‘qituvchi bahosi AI tomonidan qayta yozilmaydi
        answers = result.answers
        if answer_id is not None:
            answers = [a for a in answers if a.id == answer_id]
        if not answers:
            raise HTTPException(404, "Writing answer topilmadi")

        # Idempotency: AI feedback bor javobga provayder qayta chaqirilmaydi.
        answers = [a for a in answers if not any(f.source == FeedbackSource.AI for f in a.feedbacks)]
        if not answers:
            return await self.get_result_by_id(result_id)

        exam_task_text = build_exam_task_text(result.exam.tasks if result.exam else [])

        plans: list[dict] = []
        for answer in answers:
            task = answer.task
            try:
                spec = task_spec(task.part_number, task.sub_part)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            text = (answer.content or "").strip()
            copied = bool(text) and is_task_copy(text, exam_task_text)
            plans.append({
                "answer": answer, "task": task, "spec": spec, "words": count_words(text),
                "zero": not text or copied, "copied": copied,
            })

        ai_plans = [p for p in plans if not p["zero"]]
        evaluations: dict[int, dict] = {}
        model_name: str | None = None
        if ai_plans:
            grader = WritingAIGrader()
            model_name = grader.model
            calls = [
                grader.evaluate(spec=p["spec"], task=_task_text(p["task"]), answer=p["answer"].content, word_count=p["words"])
                for p in ai_plans
            ]
            try:
                outcomes = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), timeout=AI_TOTAL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise HTTPException(504, "AI baholash juda uzoq davom etdi. Qayta urinib ko‘ring") from exc
            for plan, outcome in zip(ai_plans, outcomes):
                if isinstance(outcome, BaseException):
                    raise outcome
                evaluations[plan["answer"].id] = outcome

        for plan in plans:
            if plan["zero"]:
                self._apply_zero_score(result, plan["answer"], plan["spec"], plan["words"], copied=plan["copied"])
            else:
                self._apply_ai_evaluation(
                    result=result, answer=plan["answer"], spec=plan["spec"], words=plan["words"],
                    data=evaluations[plan["answer"].id], model_name=model_name,
                    purchased_answer_ids=purchased_answer_ids, record_usage=record_usage,
                )

        if self._aggregate(result):
            if finalize:
                # Candidate's automatic AI evaluation is final when all tasks are scored.
                result.is_finalized = True
                result.evaluation_status = "AI_FINAL"
                await self._sync_mock_attempt(result, float(result.raw_score), float(result.scaled_score))
            else:
                # Admin/teacher-triggered AI evaluation is a draft only. It must
                # not mark the official mock-exam skill checked before human grading.
                result.is_finalized = False
                result.evaluation_status = "AI_DRAFT"
        await self.db.commit()
        return await self.get_result_by_id(result.id)

    async def _sync_mock_attempt(self, result, raw_score: float, final: float) -> None:
        """Mock imtihon bo‘lsa, natijani MockSkillAttempt'ga yozadi (yakunlangan urinishga tegmaydi)."""
        if not result.exam_attempt_id:
            return
        from app.modules.exams.mock.models import MockExamAttempt, MockSkillAttempt, SkillType
        from app.modules.exams.mock.finalize import overall_cefr_level, try_finalize_mock_attempt

        attempt = await self.db.get(MockExamAttempt, result.exam_attempt_id)
        if attempt and attempt.is_finished:
            return
        skill_row = (await self.db.execute(
            select(MockSkillAttempt).where(
                MockSkillAttempt.attempt_id == result.exam_attempt_id,
                MockSkillAttempt.skill == SkillType.WRITING,
            )
        )).scalar_one_or_none()
        if skill_row:
            skill_row.raw_score = raw_score
            skill_row.scaled_score = final
            skill_row.cefr_level = overall_cefr_level(final)
            skill_row.is_checked = True
            skill_row.submitted_at = skill_row.submitted_at or utcnow()
            await self.db.flush()
            await try_finalize_mock_attempt(self.db, result.exam_attempt_id)

    async def request_teacher_check(self, result_id: int, user_id: uuid.UUID) -> WritingResult:
        """Alohida sotib olingan o‘qituvchi tekshiruvini navbatga qo‘yadi.
        FAQAT to‘lov/kredit muvaffaqiyatli yechilgandan keyin chaqiring (billing shu yerda yo‘q).
        AI bahosi o‘qituvchi tasdiqlaguncha amalda qoladi."""
        result = await self.get_result_by_id(result_id)
        if result.user_id != user_id:
            raise HTTPException(404, "Result not found")
        if result.evaluation_status == "HUMAN_FINAL":
            raise HTTPException(409, "Natija allaqachon o‘qituvchi tomonidan tekshirilgan")
        if result.evaluation_status != "AI_FINAL":
            raise HTTPException(409, "O‘qituvchi tekshiruvi faqat AI baholab bo‘lingan natija uchun mumkin")
        # Kreditni faqat barcha holat tekshiruvlari o'tgandan keyin sarflaymiz.
        # Shartli UPDATE parallel so'rovlarda bitta kredit ikki marta ishlatilishining oldini oladi.
        from app.modules.billing.models import TeacherReviewCreditGrant
        grants = (await self.db.execute(
            select(TeacherReviewCreditGrant)
            .where(
                TeacherReviewCreditGrant.user_id == user_id,
                TeacherReviewCreditGrant.credits_remaining > 0,
            )
            .order_by(TeacherReviewCreditGrant.created_at, TeacherReviewCreditGrant.id)
            .with_for_update()
        )).scalars().all()
        consumed = False
        for grant in grants:
            update_result = await self.db.execute(
                update(TeacherReviewCreditGrant)
                .where(
                    TeacherReviewCreditGrant.id == grant.id,
                    TeacherReviewCreditGrant.user_id == user_id,
                    TeacherReviewCreditGrant.credits_remaining > 0,
                )
                .values(credits_remaining=TeacherReviewCreditGrant.credits_remaining - 1)
                .execution_options(synchronize_session=False)
            )
            if update_result.rowcount == 1:
                consumed = True
                break
        if not consumed:
            raise HTTPException(402, "Teacher tekshiruvi uchun kredit yo‘q. Billing bo‘limidan tekshiruv xarid qiling.")
        result.evaluation_status = "TEACHER_PENDING"
        await self.db.commit()
        return await self.get_result_by_id(result_id)

    @staticmethod
    def _aggregate(result) -> bool:
        """Jami = topshiriq ballari yig‘indisi (0–16), jadval bo‘yicha 0–75; daraja yakuniy balldan.
        Barcha topshiriqlar baholanmaguncha yakuniy ball va daraja qo‘yilmaydi."""
        answers = list(result.answers)
        expected = len(result.exam.tasks) if result.exam else len(answers)
        scored = [a.raw_score for a in answers if a.raw_score is not None]
        total = float(sum(scored)) if scored else None
        complete = bool(answers) and len(scored) == len(answers) and len(answers) >= expected
        result.raw_score = total
        if complete:
            scaled = expert_to_scaled(total)
            result.scaled_score = float(scaled)
            level = level_from_scaled(scaled)
            from ..models import CEFRLevel
            result.cefr_level = CEFRLevel(level) if level else None
        else:
            result.scaled_score = None
            result.cefr_level = None
        return complete

    def _set_overall_score(self, answer, value: float) -> None:
        from ..models import WritingCriterion, WritingScore
        existing = next((x for x in answer.scores if x.criterion == WritingCriterion.OVERALL), None)
        if existing:
            existing.score = value
        else:
            self.db.add(WritingScore(answer_id=answer.id, criterion=WritingCriterion.OVERALL, score=value))

    def _apply_zero_score(self, result, answer, spec, words: int, *, copied: bool) -> None:
        """Bo‘sh yoki topshiriq nusxasi javobga 0 ball. AI chaqirilmaydi, kvota sarflanmaydi."""
        from ..models import FeedbackSource, WritingFeedback

        answer.raw_score = 0.0
        answer.penalty = 0.0
        answer.word_count = words
        self._set_overall_score(answer, 0.0)
        reason = (
            "Javob topshiriq matnining nusxasi. Haqiqiy javob yozilmagani uchun 0 ball berildi."
            if copied else
            "Javob kiritilmagan. Bo‘sh qoldirilgan topshiriq uchun 0 ball berildi."
        )
        payload = {"version": 3, "summary": reason, "score": 0, "max_score": spec.max_score,
                   "word_count": words, "criteria_comments": {}, "errors": [], "corrected_text": ""}
        self.db.add(WritingFeedback(answer_id=answer.id, source=FeedbackSource.AI, model_name=None,
                                    content=json.dumps(payload, ensure_ascii=False)))
        result.ai_evaluated_at = datetime.now(timezone.utc)
        result.evaluation_status = "AI_DRAFT"
        result.is_finalized = False

    def _apply_ai_evaluation(self, *, result, answer, spec, words: int, data: dict, model_name,
                             purchased_answer_ids: set[int], record_usage: bool) -> None:
        """Model bahosi + so‘z soni chegarasi (server) → topshiriq bali."""
        from ..models import FeedbackSource, WritingFeedback

        model_score = int(data["score"])
        cap = spec.length_cap(words)
        final_score = min(model_score, cap)

        answer.raw_score = float(final_score)
        answer.penalty = 0.0
        answer.word_count = words
        self._set_overall_score(answer, float(final_score))

        payload = {
            "version": 3,
            "summary": str(data["feedback"]),
            "score": final_score,
            "max_score": spec.max_score,
            "model_score": model_score,
            "length_cap_applied": final_score < model_score,
            "word_count": words,
            "criteria_comments": data.get("criteria_comments", {}),
            "errors": data.get("errors", []),
            "corrected_text": data.get("corrected_text", ""),
        }
        self.db.add(WritingFeedback(answer_id=answer.id, source=FeedbackSource.AI, model_name=model_name,
                                    content=json.dumps(payload, ensure_ascii=False)))
        if record_usage:
            from app.modules.billing.models import WritingAIUsage
            self.db.add(WritingAIUsage(
                user_id=result.user_id, answer_id=answer.id,
                billing_source="purchased" if answer.id in purchased_answer_ids else "subscription",
            ))
        result.ai_evaluated_at = datetime.now(timezone.utc)
        result.ai_model = model_name
        result.evaluation_status = "AI_DRAFT"
        result.is_finalized = False

    # ==================================================
    # SINGLE EXAM RESULT
    # ==================================================

    # ==================================================
    # USER ALL RESULTS
    # ==================================================

    async def get_user_results(
        self,
        user_id: uuid.UUID,
    ) -> list[WritingResult]:
        stmt = (
            select(WritingResult)
            .options(selectinload(WritingResult.answers))
            .where(WritingResult.user_id == user_id)
            .order_by(WritingResult.created_at.desc())
        )
        results = await self.db.scalars(stmt)
        return list(results)

    # ==================================================
    # ADMIN: GET ALL RESULTS
    # ==================================================

    async def get_all_results(self) -> list[WritingResult]:
        stmt = (
            select(WritingResult)
            .options(selectinload(WritingResult.answers))
            .order_by(WritingResult.created_at.desc())
        )
        results = await self.db.scalars(stmt)
        return list(results)

    # ==================================================
    # GET BY RESULT ID
    # ==================================================


    async def get_result_by_id(
        self,
        result_id: int,
    ) -> WritingResult:
        stmt = (
            select(WritingResult)
            .options(
                selectinload(WritingResult.user).selectinload(User.profile),
                selectinload(WritingResult.user).selectinload(User.contacts),
                selectinload(WritingResult.exam).selectinload(WritingExam.tasks),
                selectinload(WritingResult.answers).selectinload(WritingAnswer.task),
                selectinload(WritingResult.answers).selectinload(WritingAnswer.scores),
                selectinload(WritingResult.answers).selectinload(WritingAnswer.feedbacks),
            )
            .where(WritingResult.id == result_id)
        )
        result = await self.db.scalar(stmt)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Result not found",
            )
        return result
