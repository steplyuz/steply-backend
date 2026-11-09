# app/modules/exams/listening/service.py
import logging
import math
import os
import uuid
from typing import Any, List, Optional, Tuple

import aiofiles
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import utcnow
from app.core.file_storage import generated_name, private_path, safe_suffix
from app.core.id_generator import generate_listening_id
from app.modules.exams.mock.finalize import try_finalize_mock_attempt
from app.modules.exams.mock.models import MockSkillAttempt, SkillType
from app.modules.exams.mock.usage import (
    ensure_deletable, ensure_not_in_mock_exam, ensure_no_results,
    purge_exam_versions, purge_practice_attempts, purge_results,
)
from app.modules.exams.paper.exam_paper_mixin import PaperVersionedExamMixin
from app.modules.exams.paper.models import PaperModule
from app.modules.exams.paper.schemas import LockValidationError
from app.modules.tests.scoring_policy import scale_listening_reading

from .models import (
    ListeningPart,
    ListeningPartOption,
    ListeningQuestion,
    ListeningQuestionOption,
    ListeningResult,
    ListeningTest,
)
from .schemas import (
    ListeningAudioMode,
    ListeningExamCreate,
    ListeningExamUpdate,
    ListeningSubmission,
)

logger = logging.getLogger(__name__)

# Rasmiy formal imtihon: 6 qism, 35 savol, savol raqamlari 1..35 uzluksiz.
FORMAL_PART_COUNT = 6
FORMAL_TOTAL_QUESTIONS = 35

# Audio: fayl private papkada saqlanadi, router shu kategoriyadan beradi.
# Qiymat router dagi AUDIO_CATEGORY bilan BIR XIL bo'lishi shart.
LISTENING_AUDIO_CATEGORY = "listening_audio"
LISTENING_AUDIO_URL_PREFIX = "/api/v1/exams/listening-audio"
MAX_AUDIO_BYTES = 100 * 1024 * 1024  # nginx client_max_body_size 100M bilan mos
AUDIO_CHUNK_SIZE = 1024 * 1024       # 1 MB: fayl xotiraga to'liq o'qilmaydi


def _valid_audio_signature(header: bytes, ext: str) -> bool:
    """Fayl boshidagi magic bytes kengaytmaga mos kelishini tekshiradi."""
    return (
        (header.startswith(b"RIFF") and header[8:12] == b"WAVE" and ext == ".wav")
        or (header.startswith(b"OggS") and ext == ".ogg")
        or (header.startswith(b"ID3") and ext == ".mp3")
        or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0 and ext == ".mp3")
        or (header.startswith(b"\x1a\x45\xdf\xa3") and ext == ".webm")
        or (len(header) >= 8 and header[4:8] == b"ftyp" and ext == ".m4a")
    )


class ListeningService(PaperVersionedExamMixin):
    # PaperVersionedExamMixin konfiguratsiyasi — Reading bilan umumiy
    # paper/answer-key/answer-sheet qatlami shu orqali keladi.
    PAPER_MODULE = PaperModule.LISTENING
    MODULE_LABEL = "LISTENING"
    NOT_FOUND_DETAIL = "Listening test topilmadi"
    # Eslatma: mixin `_is_partial` shu atributga tayanishi mumkin (TODO: tekshiring).
    TEMPLATE_PARTS = {1: (1, 8), 2: (9, 14), 3: (15, 18), 4: (19, 23), 5: (24, 29), 6: (30, 35)}
    TEMPLATE_ORDERED = True
    _QUESTION_MODEL = ListeningQuestion
    _PART_MODEL = ListeningPart
    _PART_EXAM_FK = "exam_id"

    def __init__(self, db: AsyncSession):
        self.db = db

    # ================================================================
    #  STRUCTURE VALIDATION
    # ================================================================
    def _validate_template_structure(self, parts, is_practice: bool = False):
        """Formal (is_practice=False) test tuzilmasini tekshiradi.

        Qoidalar:
          - qismlar soni 6 va part_number lari 1..6 (takrorsiz);
          - har bir qismda kamida bitta savol, savol raqamlari uzluksiz;
          - barcha savol raqamlari 1..35 oralig'ida, takrorsiz, to'liq;
          - qismlar savol raqamlari bo'yicha ketma-ket.

        Practice (qisman) testlar erkin qabul qilinadi.
        """
        if is_practice:
            return

        ordered = sorted(parts, key=lambda p: p.part_number)
        part_numbers = [p.part_number for p in ordered]
        if part_numbers != list(range(1, FORMAL_PART_COUNT + 1)):
            raise HTTPException(
                422,
                detail=f"Formal testda {FORMAL_PART_COUNT} ta qism bo'lishi kerak "
                       f"(part_number 1..{FORMAL_PART_COUNT}). Hozir: {part_numbers}",
            )

        all_numbers: list[int] = []
        previous_last = 0
        for part in ordered:
            numbers = sorted(q.question_number for q in part.questions)
            if not numbers:
                raise HTTPException(422, detail=f"Part {part.part_number} da savol yo'q")
            first, last = numbers[0], numbers[-1]
            if numbers != list(range(first, last + 1)):
                raise HTTPException(
                    422,
                    detail=f"Part {part.part_number} savol raqamlari uzluksiz emas: {numbers}",
                )
            if first <= previous_last:
                raise HTTPException(
                    422,
                    detail=f"Part {part.part_number} savollari oldingi qismlar bilan kesishadi",
                )
            previous_last = last
            all_numbers.extend(numbers)

        expected = list(range(1, FORMAL_TOTAL_QUESTIONS + 1))
        if sorted(all_numbers) != expected:
            missing = sorted(set(expected) - set(all_numbers))
            duplicated = sorted({n for n in all_numbers if all_numbers.count(n) > 1})
            raise HTTPException(
                422,
                detail=f"Savol raqamlari 1..{FORMAL_TOTAL_QUESTIONS} bo'yicha to'liq emas. "
                       f"Yetishmaydi: {missing}, takrorlangan: {duplicated}",
            )

    async def _load_exam_or_404(self, exam_id: str):
        exam = await self.get_exam_by_id(exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail=self.NOT_FOUND_DETAIL)
        return exam

    async def create_new_version(self, exam_id: str):
        """Create a new editable draft from the current Listening content after a locked version."""
        await self._load_exam_or_404(exam_id)
        paper = self._paper_service(exam_id)
        latest = await paper.get_answer_key()
        if latest is not None and not latest.is_locked:
            raise HTTPException(409, "Bu testda allaqachon tahrirlanadigan draft versiya mavjud.")
        questions = await self._question_dicts_for_paper(exam_id)
        version = await paper.sync_draft(questions)
        await self.db.commit()
        await self.db.refresh(version)
        return version

    async def lock_exam(self, exam_id: str, locked_by_user_id: Optional[int] = None):
        exam = await self._load_exam_or_404(exam_id)

        # Audio finalizationdan oldin tekshiriladi.
        audio_errors: list[LockValidationError] = []
        if str(exam.audio_mode or "PARTS").upper() == ListeningAudioMode.EXAM.value:
            if not (exam.audio_url or "").strip():
                audio_errors.append(LockValidationError(message="audio_mode=EXAM bo'lsa audio_url kiritilishi shart."))
            ordered_parts = sorted(exam.parts, key=lambda part: part.part_number)
            missing_segments = [
                part.part_number for part in ordered_parts
                if part.audio_start_seconds is None or part.audio_end_seconds is None
            ]
            if missing_segments:
                audio_errors.append(LockValidationError(
                    message=f"Umumiy audio ishlatilganda har bir Part uchun boshlanish va tugash sekundlarini kiriting. Yetishmayotgan Partlar: {missing_segments}"
                ))
            previous_end = None
            invalid_ranges = []
            for part in ordered_parts:
                start = part.audio_start_seconds
                end = part.audio_end_seconds
                if start is None or end is None:
                    continue
                if end <= start:
                    invalid_ranges.append(part.part_number)
                if previous_end is not None and start < previous_end:
                    invalid_ranges.append(part.part_number)
                previous_end = end
            if invalid_ranges:
                audio_errors.append(LockValidationError(
                    message=f"Audio segmentlari noto'g'ri yoki bir-birini qoplaydi. Partlarni tekshiring: {sorted(set(invalid_ranges))}"
                ))
        else:
            missing = [p.part_number for p in exam.parts if not (p.audio_url or "").strip()]
            if missing:
                audio_errors.append(LockValidationError(message=f"Quyidagi Partlar uchun audio yuklanmagan: {missing}"))
        if audio_errors:
            return None, audio_errors

        partial = self._is_partial(exam.parts, exam.is_practice)
        try:
            self._validate_template_structure(exam.parts, is_practice=exam.is_practice)
        except HTTPException as exc:
            return None, [LockValidationError(message=str(exc.detail))]
        questions = await self._question_dicts_for_paper(exam_id)
        return await self._paper_service(exam_id).lock(questions, locked_by_user_id, partial=partial)

    # ================================================================
    #  PAPER PDF (module-specific)
    # ================================================================
    async def generate_paper_pdf(self, exam_id: str) -> bytes:
        from app.modules.exams.paper.question_paper_pdf import render_listening_pdf

        exam = await self.get_exam_by_id(exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail="Listening test topilmadi")
        await self.get_locked_version(exam_id)  # faqat locked bo'lsa davom etadi

        parts_payload = []
        for part in sorted(exam.parts, key=lambda p: p.part_number):
            questions_payload = [
                {
                    "question_number": q.question_number,
                    "type": q.type.value if hasattr(q.type, "value") else q.type,
                    "text": q.text,
                    "group_title": q.group_title,
                    "options": [{"label": o.label, "value": o.value} for o in q.options],
                }
                for q in sorted(part.questions, key=lambda q: q.question_number)
            ]
            parts_payload.append({
                "part_number": part.part_number,
                "title": part.title,
                "instruction": part.instruction,
                "task_type": part.task_type,
                "context": part.context,
                "passage": part.passage,
                "image_url": part.image_url,
                "map_image": part.map_image,
                "example_json": part.example_json,
                "options": [{"label": o.label, "value": o.value} for o in part.options],
                "questions": questions_payload,
            })

        exam_payload = {
            "title": exam.title,
            "cefr_level": exam.cefr_level,
            "language": "en",
            "duration_minutes": exam.duration_minutes,
            "parts": parts_payload,
        }
        return render_listening_pdf(exam_payload)

    # ================================================================
    #  SCORING LOGIC
    # ================================================================
    def _calculate_listening_metrics(self, correct_count: int) -> Tuple[float, str]:
        """Raw → (ball, CEFR). Formula yagona manbada: scoring_policy.scale_listening_reading."""
        return scale_listening_reading(correct_count)

    # ================================================================
    #  HELPERS (struktura yaratish)
    # ================================================================
    async def _create_structure(self, exam_id: str, parts_data: List[Any]):
        for p_data in parts_data:
            new_part = ListeningPart(
                exam_id=exam_id,
                external_id=p_data.external_id or str(uuid.uuid4()),
                part_number=p_data.part_number,
                title=p_data.title,
                instruction=p_data.instruction,
                task_type=p_data.task_type,
                audio_url=(p_data.audio_url or ""),
                audio_start_seconds=p_data.audio_start_seconds,
                audio_end_seconds=p_data.audio_end_seconds,
                context=p_data.context,
                passage=p_data.passage,
                map_image=p_data.map_image,
            )
            self.db.add(new_part)
            await self.db.flush()

            for opt in (p_data.options or []):
                self.db.add(ListeningPartOption(
                    part_id=new_part.id,
                    external_id=opt.external_id or str(uuid.uuid4()),
                    value=opt.value,
                    label=opt.label,
                ))

            for q_data in p_data.questions:
                new_q = ListeningQuestion(
                    part_id=new_part.id,
                    external_id=q_data.external_id or str(uuid.uuid4()),
                    question_number=q_data.question_number,
                    type=q_data.type,
                    text=q_data.text,
                    correct_answer=q_data.correct_answer,
                )
                self.db.add(new_q)
                await self.db.flush()

                for opt in (q_data.options or []):
                    self.db.add(ListeningQuestionOption(
                        question_id=new_q.id,
                        external_id=opt.external_id or str(uuid.uuid4()),
                        value=opt.value,
                        label=opt.label,
                    ))

    # ================================================================
    #  CRUD
    # ================================================================
    @staticmethod
    def _validate_audio_config(data: ListeningExamCreate | ListeningExamUpdate, parts=None):
        mode = getattr(data, "audio_mode", None)
        mode_value = mode.value if hasattr(mode, "value") else mode
        if mode_value == ListeningAudioMode.EXAM.value:
            if not (getattr(data, "audio_url", None) or "").strip():
                raise HTTPException(422, detail="audio_mode=EXAM bo'lganda audio_url majburiy.")
        elif mode_value == ListeningAudioMode.PARTS.value:
            source_parts = parts if parts is not None else getattr(data, "parts", None)
            if source_parts is not None:
                missing = [p.part_number for p in source_parts if not (getattr(p, "audio_url", None) or "").strip()]
                if missing:
                    raise HTTPException(422, detail=f"audio_mode=PARTS bo'lganda har bir part uchun audio kerak: {missing}")

    async def upload_audio(self, file: UploadFile) -> dict:
        """Audioni private papkaga chunk-chunk yozadi.

        Fayl xotiraga to'liq o'qilmaydi. Magic bytes kengaytmaga mos bo'lishi shart.
        Xato bo'lsa yarim yozilgan fayl o'chiriladi.
        """
        allowed = {".mp3", ".m4a", ".wav", ".ogg", ".webm"}
        ext = safe_suffix(os.path.splitext(file.filename or "")[1].lower(), allowed)

        filename = generated_name(ext)
        try:
            path = private_path(LISTENING_AUDIO_CATEGORY, filename)
        except ValueError as exc:
            logger.error("Unsafe listening audio storage path: %s", exc)
            raise HTTPException(400, detail="Audio saqlash yo'li noto'g'ri.")

        size = 0
        header = b""
        try:
            async with aiofiles.open(path, "wb") as f:
                while chunk := await file.read(AUDIO_CHUNK_SIZE):
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise HTTPException(413, detail="Audio hajmi 100 MB dan oshmasligi kerak.")
                    if len(header) < 16:
                        header += chunk[: 16 - len(header)]
                    await f.write(chunk)

            if size == 0:
                raise HTTPException(400, detail="Audio fayl bo'sh.")
            if not _valid_audio_signature(header, ext):
                raise HTTPException(415, detail="Audio fayl tarkibi kengaytmaga mos kelmadi.")
        except HTTPException:
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            logger.exception("Listening audio upload failed: %s", exc)
            raise HTTPException(500, detail="Audio yuklashda xatolik yuz berdi.")

        return {
            "url": f"{LISTENING_AUDIO_URL_PREFIX}/{filename}",
            "filename": filename,
            "size": size,
        }

    async def create_exam(self, data: ListeningExamCreate):
        try:
            data.parts = sorted(data.parts, key=lambda part: part.part_number)
            if "is_practice" not in data.model_fields_set and self._is_partial(data.parts):
                data.is_practice = True
            self._validate_template_structure(data.parts, is_practice=data.is_practice)

            exam_id = data.id or generate_listening_id()
            if data.id and await self.db.get(ListeningTest, exam_id):
                raise HTTPException(409, "Bu test ID allaqachon mavjud")
            computed_total = sum(len(p.questions) for p in data.parts)
            total_questions = data.total_questions if data.total_questions is not None else computed_total
            new_exam = ListeningTest(
                id=exam_id,
                title=data.title,
                is_demo=data.is_demo,
                is_free=data.is_free,
                is_practice=data.is_practice,
                is_active=data.is_active,
                sections=data.sections,
                audio_mode=data.audio_mode.value if hasattr(data.audio_mode, "value") else data.audio_mode,
                audio_url=data.audio_url,
                cefr_level=data.cefr_level,
                duration_minutes=data.duration_minutes,
                total_questions=total_questions,
            )
            self.db.add(new_exam)
            await self.db.flush()

            await self._create_structure(new_exam.id, data.parts)
            await self.db.commit()
            return await self.get_exam_by_id(new_exam.id)
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            logger.exception("Error creating listening exam: %s", e)
            raise HTTPException(400, detail="Listening imtihonini yaratib bo'lmadi. Ma'lumotlarni tekshiring.")

    async def create_part_mini_tests(self, exam_id: str) -> dict:
        """Har bir partdan mustaqil Listening mini-test yaratadi; takroriy chaqiruv dublikat yaratmaydi."""
        source = await self.get_exam_by_id(exam_id)
        if not source:
            raise HTTPException(status_code=404, detail="Listening test topilmadi")
        if source.parent_test_id is not None:
            raise HTTPException(status_code=400, detail="Mini-testdan yana mini-test yaratib bo'lmaydi")
        if not source.parts:
            raise HTTPException(status_code=400, detail="Testda partlar mavjud emas")
        if any(not part.questions for part in source.parts):
            raise HTTPException(status_code=400, detail="Mini-test yaratish uchun har bir partda kamida bitta savol bo'lishi kerak")

        ordered_parts = sorted(source.parts, key=lambda item: item.part_number)
        mode = str(source.audio_mode or "PARTS").upper()
        is_exam_audio = mode == ListeningAudioMode.EXAM.value

        if is_exam_audio:
            if not (source.audio_url or "").strip():
                raise HTTPException(status_code=400, detail="audio_mode=EXAM bo'lsa umumiy audio_url kiritilishi shart")
            missing = [p.part_number for p in ordered_parts if p.audio_start_seconds is None or p.audio_end_seconds is None]
            if missing:
                raise HTTPException(status_code=400, detail=f"Quyidagi Partlar uchun audio boshlanish/tugash sekundlari yo'q: {missing}")
        else:
            missing = [p.part_number for p in ordered_parts if not (p.audio_url or "").strip()]
            if missing:
                raise HTTPException(status_code=400, detail=f"Quyidagi Partlar uchun audio yuklanmagan: {missing}")

        existing_rows = await self.db.execute(
            select(ListeningTest).where(ListeningTest.parent_test_id == source.id)
        )
        existing = {row.practice_part_number: row for row in existing_rows.scalars().all()}
        created_ids: list[str] = []
        new_count = 0
        part_count = len(ordered_parts)

        for part in ordered_parts:
            mini = existing.get(part.part_number)
            if mini is not None:
                created_ids.append(mini.id)
                continue

            if is_exam_audio:
                seconds = float(part.audio_end_seconds) - float(part.audio_start_seconds)
                minutes = max(3, math.ceil(seconds / 60) + 1)
            else:
                minutes = max(3, round((source.duration_minutes or 35) / part_count))

            mini = ListeningTest(
                id=generate_listening_id(),
                title=f"{source.title} — Part {part.part_number}: {part.title}",
                cefr_level=source.cefr_level,
                duration_minutes=minutes,
                total_questions=len(part.questions),
                sections=source.sections,
                audio_mode=source.audio_mode or "PARTS",
                audio_url=source.audio_url if is_exam_audio else None,
                is_demo=False,
                is_free=bool(source.is_free),
                is_practice=True,
                is_active=bool(source.is_active),
                parent_test_id=source.id,
                practice_part_number=part.part_number,
            )
            mini_part = ListeningPart(
                external_id=str(uuid.uuid4()),
                part_number=part.part_number,
                title=part.title,
                instruction=part.instruction,
                task_type=part.task_type,
                audio_url=part.audio_url or "",
                audio_start_seconds=part.audio_start_seconds,
                audio_end_seconds=part.audio_end_seconds,
                context=part.context,
                passage=part.passage,
                map_image=part.map_image,
            )
            for opt in part.options:
                mini_part.options.append(ListeningPartOption(
                    external_id=str(uuid.uuid4()), value=opt.value, label=opt.label,
                ))
            for q in sorted(part.questions, key=lambda item: item.question_number):
                mini_q = ListeningQuestion(
                    external_id=str(uuid.uuid4()),
                    question_number=q.question_number,
                    type=q.type,
                    text=q.text,
                    correct_answer=q.correct_answer,
                )
                for opt in q.options:
                    mini_q.options.append(ListeningQuestionOption(
                        external_id=str(uuid.uuid4()), value=opt.value, label=opt.label,
                    ))
                mini_part.questions.append(mini_q)
            mini.parts.append(mini_part)
            self.db.add(mini)
            created_ids.append(mini.id)
            new_count += 1

        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            logger.exception("Error creating listening part mini-tests: %s", exc)
            raise HTTPException(400, detail="Listening mini-testlarini yaratib bo'lmadi.")

        rows = (await self.db.execute(
            select(ListeningTest)
            .where(ListeningTest.id.in_(created_ids))
            .order_by(ListeningTest.practice_part_number.asc())
        )).scalars().all()
        return {
            "source_test_id": source.id,
            "created_count": new_count,
            "total_count": len(rows),
            "tests": [
                {"id": row.id, "title": row.title, "practice_part_number": row.practice_part_number}
                for row in rows
            ],
        }

    async def get_all_exams(
        self,
        *,
        is_active: Optional[bool] = None,
        is_practice: Optional[bool] = None,
        is_free: Optional[bool] = None,
        only_ids: Optional[List[str]] = None,
    ):
        stmt = select(ListeningTest).order_by(ListeningTest.created_at.desc())
        if is_active is not None:
            stmt = stmt.where(ListeningTest.is_active.is_(is_active))
        if is_practice is not None:
            stmt = stmt.where(ListeningTest.is_practice.is_(is_practice))
        if is_free is not None:
            stmt = stmt.where(ListeningTest.is_free.is_(is_free))
        if only_ids is not None:
            stmt = stmt.where(ListeningTest.id.in_(only_ids))
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_exam_by_id(self, exam_id: str):
        stmt = select(ListeningTest).where(ListeningTest.id == exam_id).options(
            selectinload(ListeningTest.parts).options(
                selectinload(ListeningPart.questions).selectinload(ListeningQuestion.options),
                selectinload(ListeningPart.options),
            )
        )
        res = await self.db.execute(stmt)
        return res.unique().scalar_one_or_none()

    async def update_exam(self, exam_id: str, data: ListeningExamUpdate):
        locked = await self._paper_service(exam_id).get_answer_key()
        if locked and locked.is_locked:
            raise HTTPException(409, "Listening test locked qilingan. Yangi versiya yarating yoki lockni o'zgartirmang.")
        exam = await self.db.get(ListeningTest, exam_id)
        if not exam:
            raise HTTPException(404, detail="Imtihon topilmadi")

        if data.parts is not None:
            data.parts = sorted(data.parts, key=lambda part: part.part_number)
            practice = data.is_practice if getattr(data, "is_practice", None) is not None else exam.is_practice
            self._validate_template_structure(data.parts, is_practice=practice)

        update_data = data.model_dump(exclude_unset=True)
        if "audio_mode" in update_data:
            mode = update_data["audio_mode"]
            update_data["audio_mode"] = mode.value if hasattr(mode, "value") else mode
        for key, value in update_data.items():
            if key != "parts":
                setattr(exam, key, value)

        if data.parts is not None:
            old_parts = (await self.db.execute(
                select(ListeningPart).where(ListeningPart.exam_id == exam_id)
            )).scalars().all()
            for old in old_parts:
                await self.db.delete(old)
            await self.db.flush()
            self.db.expire(exam, ["parts"])
            await self._create_structure(exam.id, data.parts)

        await self.db.commit()
        return await self.get_exam_by_id(exam_id)

    async def delete_exam(self, exam_id: str, force: bool = False):
        exam = await self.db.get(ListeningTest, exam_id)
        if not exam:
            return False
        await ensure_deletable(
            self.db, skill="listening", module=PaperModule.LISTENING, test_id=exam_id,
            result_model=ListeningResult, result_col=ListeningResult.exam_id, force=force,
        )
        await purge_results(self.db, ListeningResult, ListeningResult.exam_id, exam_id)
        await purge_practice_attempts(self.db, "listening", exam_id)
        await purge_exam_versions(self.db, PaperModule.LISTENING, exam_id)
        await self.db.delete(exam)
        await self.db.commit()
        return True

    # ================================================================
    #  RESULTS & SUBMISSION
    # ================================================================
    async def submit_exam_and_get_result(self, user_id: uuid.UUID, data: ListeningSubmission):
        """Javoblarni yuborish. Baholash saqlangan javoblardan server tomonda bajariladi."""
        from app.modules.tests.attempts import common as at
        from app.modules.tests.attempts import submit as _sub

        exam = await self.get_exam_by_id(data.exam_id)
        if not exam:
            raise HTTPException(404, detail="Imtihon topilmadi")
        numbers = {q.question_number for p in exam.parts for q in p.questions}
        incoming = {}
        for key, value in (data.user_answers or {}).items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            if number in numbers:
                incoming[number] = at.normalize_answer(value)

        result = await _sub.submit_with_answers(
            self.db, at.spec_for(SkillType.LISTENING), user_id, exam, data.exam_attempt_id, incoming
        )
        return await self.get_result_with_review(result.id, user_id)

    async def get_user_results(self, user_id: uuid.UUID):
        stmt = (
            select(ListeningResult)
            .where(ListeningResult.user_id == user_id)
            .order_by(ListeningResult.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_result_with_review(self, result_id: int, user_id: uuid.UUID):
        """Natija va savollar tahlilini qaytarish."""
        stmt = select(ListeningResult).where(
            ListeningResult.id == result_id,
            ListeningResult.user_id == user_id,
        )
        res = await self.db.execute(stmt)
        result_data = res.scalar_one_or_none()
        if not result_data:
            return None

        exam = await self.get_exam_by_id(result_data.exam_id)
        if not exam:
            return None

        review_data = []
        for part in exam.parts:
            for q in part.questions:
                raw_answer = result_data.user_answers.get(str(q.question_number), "")
                u_ans = raw_answer.strip().lower()
                valid_options = [a.strip() for a in q.correct_answer.strip().lower().split("/") if a.strip()]
                is_correct = bool(u_ans) and u_ans in valid_options

                review_data.append({
                    "question_id": q.id,
                    "question_number": q.question_number,
                    "user_answer": raw_answer,
                    "correct_answer": q.correct_answer,
                    "is_correct": is_correct,
                    "type": q.type,
                })

        return {
            "summary": result_data,
            "review": review_data,
        }

    # ================================================================
    #  MOCK EXAM INTEGRATION (INTERNAL)
    # ================================================================
    async def _update_mock_listening(
        self, exam_attempt_id: int, score: float, cefr_level: str, user_id: uuid.UUID,
        raw_score: float = 0.0,
    ):
        # IDOR himoyasi: exam_attempt_id client tomonidan keladi, shuning uchun
        # faqat foydalanuvchining o'z skill qatori yangilanadi.
        stmt = select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == exam_attempt_id,
            MockSkillAttempt.skill == SkillType.LISTENING,
            MockSkillAttempt.user_id == user_id,
        )
        skill = (await self.db.execute(stmt)).scalar_one_or_none()
        if skill:
            from app.modules.exams.mock.models import MockExamAttempt
            attempt = await self.db.get(MockExamAttempt, exam_attempt_id)
            if attempt and attempt.is_finished:
                raise HTTPException(409, "Yakunlangan imtihon natijasini o'zgartirib bo'lmaydi")
            skill.raw_score = float(raw_score)
            skill.scaled_score = float(score)
            skill.cefr_level = cefr_level
            skill.is_checked = True
            skill.submitted_at = utcnow()
            await self.db.flush()
            await try_finalize_mock_attempt(self.db, exam_attempt_id)