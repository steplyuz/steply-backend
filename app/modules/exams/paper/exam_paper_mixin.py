"""Reading va Listening servislari uchun UMUMIY qog'oz (paper) qatlami.

Ilgari quyidagi metodlar `reading_test/services.py` va
`listening_test/services.py` da deyarli belgi-ma-belgi takrorlangan edi
(~140 qator): `_paper_service`, `_question_dicts_for_paper`,
`get_answer_key`, `list_versions`, `get_locked_version`,
`generate_answer_sheet_pdf`, `_validate_template_structure`.

Endi yagona manba shu mixin. Har bir modul faqat quyidagilarni beradi:

    PAPER_MODULE          — PaperModule.READING / PaperModule.LISTENING
    MODULE_LABEL          — javob varaqasidagi sarlavha ("READING")
    NOT_FOUND_DETAIL      — 404 matni
    _QUESTION_MODEL       — ReadingQuestion / ListeningQuestion
    _PART_MODEL           — ReadingPart / ListeningPart
    _PART_EXAM_FK         — Part modelidagi imtihon FK nomi ("test_id"/"exam_id")
    TEMPLATE_PARTS        — {part_number: (birinchi_savol, oxirgi_savol)}
    TEMPLATE_ORDERED      — part_number tartibi ham tekshirilsinmi

va `_load_exam_or_404(exam_id)` ni amalga oshiradi.
"""

from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .models import PaperModule
from .service import PaperVersionService


class PaperVersionedExamMixin:
    # --- pastki sinf to'ldiradigan konfiguratsiya ---
    PAPER_MODULE: PaperModule
    MODULE_LABEL: str = ""
    NOT_FOUND_DETAIL: str = "Test topilmadi"
    TEMPLATE_PARTS: Dict[int, Tuple[int, int]] = {}
    TEMPLATE_ORDERED: bool = False
    TEMPLATE_TOTAL_QUESTIONS: int = 35

    _QUESTION_MODEL: Any = None
    _PART_MODEL: Any = None
    _PART_EXAM_FK: str = "exam_id"

    # Test o'chirish/tahrirlash bog'liqligi (mock/usage.py) uchun — subclass to'ldiradi
    USAGE_SKILL: str = ""          # "reading" | "listening"
    RESULT_MODEL: Any = None         # ReadingResult / ListeningResult
    RESULT_COL: Any = None           # ReadingResult.test_id / ListeningResult.exam_id

    # --- pastki sinf amalga oshiradi ---
    async def _load_exam_or_404(self, exam_id: str):
        raise NotImplementedError

    # ================================================================
    #  VERSIONING / ANSWER KEY
    # ================================================================
    def _paper_service(self, exam_id: str) -> PaperVersionService:
        return PaperVersionService(self.db, module=self.PAPER_MODULE, exam_id=exam_id)

    async def _question_dicts_for_paper(self, exam_id: str) -> list:
        """Joriy savollarni PaperVersionService tushunadigan dict ro'yxatiga o'giradi."""
        q_model, p_model = self._QUESTION_MODEL, self._PART_MODEL
        result = await self.db.execute(
            select(q_model)
            .join(p_model, q_model.part_id == p_model.id)
            .where(getattr(p_model, self._PART_EXAM_FK) == exam_id)
            .options(selectinload(q_model.options))
            .order_by(q_model.question_number)
        )
        return [
            {
                "question_number": q.question_number,
                "correct_answer": q.correct_answer,
                "type": q.type.value if hasattr(q.type, "value") else q.type,
                "options_count": len(q.options),
            }
            for q in result.scalars().all()
        ]

    async def get_answer_key(self, exam_id: str, version_number: Optional[int] = None):
        await self._load_exam_or_404(exam_id)
        version = await self._paper_service(exam_id).get_answer_key(version_number)
        if not version:
            raise HTTPException(
                status_code=404,
                detail="Bu test uchun hali answer key generatsiya qilinmagan "
                       "(draft yarating yoki lock qiling)",
            )
        return version

    async def list_versions(self, exam_id: str):
        return await self._paper_service(exam_id).list_versions()

    async def create_new_version(self, exam_id: str):
        """Lock qilingan testni tahrirlash uchun yangi DRAFT versiya ochadi.

        Eski lock versiyasi tarix sifatida qoladi (qog'oz/OMR va natijalar unga bog'liq).
        Draft allaqachon bo'lsa — o'sha draft qaytariladi (idempotent).
        """
        from app.modules.exams.mock.usage import ensure_editable

        await self._load_exam_or_404(exam_id)
        paper = self._paper_service(exam_id)
        latest = await paper.get_answer_key()
        if latest is not None and not latest.is_locked:
            return latest
        await ensure_editable(
            self.db, skill=self.USAGE_SKILL, module=self.PAPER_MODULE, test_id=exam_id,
            result_model=self.RESULT_MODEL, result_col=self.RESULT_COL,
        )
        questions = await self._question_dicts_for_paper(exam_id)
        version = await paper.sync_draft(questions)
        await self.db.commit()
        await self.db.refresh(version)
        return version

    async def get_locked_version(self, exam_id: str):
        version = await self._paper_service(exam_id).get_answer_key(version_number=None)
        if not version or not version.is_locked:
            raise HTTPException(
                status_code=400,
                detail="Cannot generate PDF. Test is not locked yet — call /lock first "
                       "and fix all validation errors.",
            )
        return version

    # ================================================================
    #  TEMPLATE VALIDATSIYA
    # ================================================================
    @classmethod
    def _is_partial(cls, parts, is_practice: bool = False) -> bool:
        """Qismlarga ajratilgan (part-only) yoki practice test — to'liq shablon talab qilinmaydi."""
        return bool(is_practice) or len(parts) != len(cls.TEMPLATE_PARTS)

    @staticmethod
    def _min_q(part) -> int:
        nums = [q.question_number for q in part.questions]
        return min(nums) if nums else 10**9

    @classmethod
    def sort_parts_by_question(cls, parts) -> list:
        """Partlarni eng kichik savol raqami bo'yicha tartiblaydi (Part 5,4,2.. -> 1,2,3..)."""
        return sorted(parts, key=cls._min_q)

    @classmethod
    def _validate_part_structure(cls, parts) -> None:
        """Bitta/bir nechta part uchun yengil tekshiruv: bo'sh emas, raqamlar takrorlanmaydi va uzluksiz."""
        label = cls.MODULE_LABEL.capitalize() or "Test"
        if not parts:
            raise HTTPException(422, f"{label}: kamida bitta part kerak")
        seen: list[int] = []
        for idx, p in enumerate(parts, start=1):
            nums = sorted(q.question_number for q in p.questions)
            if not nums:
                raise HTTPException(422, f"{label} Part {idx}: savollar yo'q")
            if nums != list(range(nums[0], nums[0] + len(nums))):
                raise HTTPException(
                    422, f"{label} Part {idx}: savol raqamlari uzluksiz va takrorlanmas bo'lishi kerak ({nums})"
                )
            seen.extend(nums)
        if len(seen) != len(set(seen)):
            raise HTTPException(422, f"{label}: partlar orasida savol raqamlari takrorlangan")

    @classmethod
    def _validate_template_structure(cls, parts, is_practice: bool = False) -> None:
        """To'liq test -> qat'iy shablon; part-only/practice -> yengil tekshiruv."""
        if cls._is_partial(parts, is_practice):
            cls._validate_part_structure(parts)
            return

        expected = cls.TEMPLATE_PARTS
        label = cls.MODULE_LABEL.capitalize() or "Test"
        part_count = len(expected)

        if cls.TEMPLATE_ORDERED:
            if [p.part_number for p in parts] != list(range(1, part_count + 1)):
                raise HTTPException(
                    422, f"{label} template {part_count} ta Part va 1..{part_count} tartibini talab qiladi"
                )
            ordered = list(parts)
        else:
            # Tartib JSON'dagi joylashuvga bog'liq bo'lmasin: savol raqami bo'yicha saralaymiz.
            ordered = cls.sort_parts_by_question(parts)

        all_numbers: list[int] = []
        for idx, p in enumerate(ordered, start=1):
            key = p.part_number if cls.TEMPLATE_ORDERED else idx
            lo, hi = expected[key]
            nums = sorted(q.question_number for q in p.questions)
            if nums != list(range(lo, hi + 1)):
                raise HTTPException(
                    422,
                    f"{label} Part {key}: savollar {lo}-{hi} bo'lishi kerak (hozir: {nums[0] if nums else '-'}"
                    f"..{nums[-1] if nums else '-'}, {len(nums)} ta)",
                )
            all_numbers.extend(nums)

        total = cls.TEMPLATE_TOTAL_QUESTIONS
        if sorted(all_numbers) != list(range(1, total + 1)):
            raise HTTPException(422, f"{label} jami {total} ta savol bo'lishi kerak")

    # ================================================================
    #  ANSWER SHEET PDF
    # ================================================================
    async def generate_answer_sheet_pdf(self, exam_id: str) -> bytes:
        from .answer_sheet_pdf import get_or_freeze_layout, render_answer_sheet_pdf

        exam = await self._load_exam_or_404(exam_id)
        version = await self.get_locked_version(exam_id)

        type_rows = await self._question_dicts_for_paper(exam_id)
        number_to_type = {q["question_number"]: q["type"] for q in type_rows}

        layout_json = get_or_freeze_layout(version, number_to_type)
        # answer_sheet_layout saqlanadi (agar yangi hisoblangan bo'lsa)
        await self.db.flush()

        return render_answer_sheet_pdf(
            title=exam.title,
            module_label=self.MODULE_LABEL,
            candidate_placeholder=True,
            layout_json=layout_json,
        )
