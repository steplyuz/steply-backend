import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .admin_service import WritingAdminService
from .pdf_service import PDFService
from .result_service import WritingResultService


class WritingService:
    """
    Facade layer for the CEFR Writing module.

    Responsible for:
    - Exam management
    - Submission processing
    - Result retrieval and grading (human and AI-draft)
    - PDF generation

    Router va boshqa modullar faqat shu facade orqali ishlaydi; metod imzolari
    o'zgarsa, ularni chaqiruvchilar (writing/router.py, exams/auth/router.py) ham
    yangilanishi kerak.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

        # Core services
        self.admin_service = WritingAdminService(db)
        self.result_service = WritingResultService(db)
        self.pdf_service = PDFService()

    # =====================================================
    # EXAMS
    # =====================================================

    async def create_exam(self, data):
        return await self.admin_service.create_exam(data)

    async def list_exams(self, active_only: bool = False):
        return await self.admin_service.list_exams(active_only=active_only)

    async def get_exam(self, exam_id: str):
        return await self.admin_service.get_exam_by_id(exam_id)

    async def update_exam(self, exam_id: str, data):
        return await self.admin_service.update_exam(exam_id, data)

    async def delete_exam(self, exam_id: str):
        return await self.admin_service.delete_exam(exam_id)

    async def lock_exam(self, exam_id: str, locked_by_user_id=None):
        version, errors = await self.admin_service.lock_exam(exam_id, locked_by_user_id)
        if not errors:
            await self.db.commit()
        return version, errors

    # =====================================================
    # SUBMISSION
    # =====================================================

    async def submit_practice_answers(self, user_id: uuid.UUID, exam_id: str, data):
        return await self.result_service.submit_practice_answers(user_id, exam_id, data)

    async def list_pending(self):
        return await self.result_service.list_pending()

    async def grade_result(
        self,
        result_id: int,
        raw_score: float,
        cefr_level,
        feedback,
        criterion_scores=None,
        evaluator_id=None,
        answer_id=None,
    ):
        return await self.result_service.grade_result(
            result_id,
            raw_score,
            cefr_level,
            feedback,
            criterion_scores,
            evaluator_id,
            answer_id=answer_id,
        )

    # =====================================================
    # RESULTS
    # =====================================================

    async def get_user_results(self, user_id: uuid.UUID):
        return await self.result_service.get_user_results(user_id)

    async def get_all_results(self):
        return await self.result_service.get_all_results()

    async def ai_grade_result(
        self,
        result_id: int,
        answer_id: int | None = None,
        purchased_answer_ids: set[int] | None = None,
        record_usage: bool = True,
        finalize: bool = True,
    ):
        return await self.result_service.ai_grade_result(
            result_id,
            answer_id,
            purchased_answer_ids=purchased_answer_ids,
            record_usage=record_usage,
            finalize=finalize,
        )

    async def request_teacher_check(self, result_id: int, user_id: uuid.UUID):
        return await self.result_service.request_teacher_check(result_id, user_id)

    async def get_result(self, result_id: int):
        # Mavjud bo'lmasa result_service 404 HTTPException ko'taradi.
        return await self.result_service.get_result_by_id(result_id)

    # =====================================================
    # PDF
    # =====================================================

    async def generate_pdf_report(self, result_id: int):
        # PDFService to'liq WritingResult ob'ektini kutadi (router ham shunday chaqiradi).
        result = await self.result_service.get_result_by_id(result_id)
        return await self.pdf_service.generate_pdf_report(result)