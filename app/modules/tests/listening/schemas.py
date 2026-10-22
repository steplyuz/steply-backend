from app.core.paths import LISTENING_AUDIO_DIR
import logging
from app.modules.tests.scoring_policy import scale_listening_reading
from typing import List, Any, Tuple, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, UploadFile
import math
import os
import uuid

# Mock Models (Integratsiya uchun)
from app.modules.exams.mock.models import MockSkillAttempt, SkillType
from app.modules.exams.mock.usage import (
    ensure_deletable, ensure_not_in_mock_exam, ensure_no_results,
    purge_exam_versions, purge_practice_attempts, purge_results,
)

from .models import (
    ListeningTest, 
    ListeningPart, 
    ListeningQuestion, 
    ListeningQuestionOption, 
    ListeningPartOption,
    ListeningResult
)
from .schemas import ListeningExamCreate, ListeningExamUpdate, ListeningSubmission, ListeningAudioMode

from app.modules.exams.paper.models import PaperModule
from app.modules.exams.paper.schemas import LockValidationError
from app.modules.exams.paper.exam_paper_mixin import PaperVersionedExamMixin
from app.modules.exams.mock.finalize import try_finalize_mock_attempt
from app.core.id_generator import generate_listening_id
from app.core.file_storage import generated_name, safe_suffix
from app.core.clock import utcnow

logger = logging.getLogger(__name__)

class ListeningService(PaperVersionedExamMixin):
    # PaperVersionedExamMixin konfiguratsiyasi — Reading bilan umumiy
    # paper/answer-key/answer-sheet qatlami shu orqali keladi.
    PAPER_MODULE = PaperModule.LISTENING
    MODULE_LABEL = "LISTENING"
    NOT_FOUND_DETAIL = "Listening test topilmadi"
    TEMPLATE_PARTS = {1: (1, 8), 2: (9, 14), 3: (15, 18), 4: (19, 23), 5: (24, 29), 6: (30, 35)}
    TEMPLATE_ORDERED = True
    _QUESTION_MODEL = ListeningQuestion
    _PART_MODEL = ListeningPart
    _PART_EXAM_FK = "exam_id"

    def __init__(self, db: AsyncSession):
        self.db = db

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

        # Audio finalizationdan oldin tekshiriladi — missing audio bo'lsa
        # version tasodifan locked bo'lib qolmaydi. (Bu tekshiruv faqat
        # Listeningga tegishli, shuning uchun mixinga ko'chirilmadi.)
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
    #  SCORING LOGIC (BAHOLASH)
    # ================================================================
    def _calculate_listening_metrics(self, correct_count: int) -> Tuple[float, str]:
        """Raw → (ball, CEFR). Formula yagona manbada: scoring_policy.scale_listening_reading."""
        return scale_listening_reading(correct_count)

    # ================================================================
    #  HELPERS (Struktura yaratish)
    # ================================================================
    async def _create_structure(self, exam_id: str, parts_data: List[Any]):
        for part_index, p_data in enumerate(parts_data, start=1):
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
                map_image=p_data.map_image
            )
            self.db.add(new_part)
            await self.db.flush()

            if p_data.options:
                for opt_index, opt in enumerate(p_data.options, start=1):
                    self.db.add(ListeningPartOption(part_id=new_part.id, external_id=opt.external_id or str(uuid.uuid4()), value=opt.value, label=opt.label))

            for q_index, q_data in enumerate(p_data.questions, start=1):
                new_q = ListeningQuestion(
                    part_id=new_part.id,
                    external_id=q_data.external_id or str(uuid.uuid4()),
                    question_number=q_data.question_number,
                    type=q_data.type,
                    text=q_data.text, 
                    correct_answer=q_data.correct_answer
                )
                self.db.add(new_q)
                await self.db.flush()

                if q_data.options:
                    for opt_index, opt in enumerate(q_data.options, start=1):
                        self.db.add(ListeningQuestionOption(question_id=new_q.id, external_id=opt.external_id or str(uuid.uuid4()), value=opt.value, label=opt.label))

    # ================================================================
    #  CRUD METHODS
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
        allowed = {".mp3", ".m4a", ".wav", ".ogg", ".webm"}
        ext = safe_suffix(os.path.splitext(file.filename or "")[1].lower(), allowed)
        max_bytes = 50 * 1024 * 1024
        data = await file.read(max_bytes + 1)
        if not data:
            raise HTTPException(400, detail="Audio fayl bo'sh.")
        if len(data) > max_bytes:
            raise HTTPException(413, detail="Audio hajmi 50 MB dan oshmasligi kerak.")
        valid = (
            (data.startswith(b"RIFF") and data[8:12] == b"WAVE" and ext == ".wav")
            or (data.startswith(b"OggS") and ext == ".ogg")
            or (data.startswith(b"ID3") and ext == ".mp3")
            or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0 and ext == ".mp3")
            or (data.startswith(b"\x1a\x45\xdf\xa3") and ext == ".webm")
            or (len(data) >= 8 and data[4:8] == b"ftyp" and ext == ".m4a")
        )
        if not valid:
            raise HTTPException(415, detail="Audio fayl tarkibi kengaytmaga mos kelmadi.")
        directory = LISTENING_AUDIO_DIR
        directory.mkdir(parents=True, exist_ok=True)
        filename = generated_name(ext)
        path = directory / filename
        import aiofiles
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return {"url": f"/api/v1/exams/listening-audio/{filename}", "filename": filename, "size": len(data)}

    async def create_exam(self, data: ListeningExamCreate):
        try:
            # Faqat to'liq 5 ta part bo'lgan formal exam uchun qat'iy tekshiriladi.
            # Qismlarga ajratilgan yoki practice testlar erkin qabul qilinadi.
            # Partlar part_number bo'yicha tartiblanadi; part-only test practice sanaladi.
            data.parts = sorted(data.parts, key=lambda part: part.part_number)
            if "is_practice" not in data.model_fields_set and self._is_partial(data.parts):
                data.is_practice = True
            self._validate_template_structure(data.parts, is_practice=data.is_practice)
            # Audio draft bosqichida majburiy emas. Final PDF/OMR uchun /lock audio
            # konfiguratsiyasini qat'iy tekshiradi. Bu JSON importni audio keyinroq
            # yuklanadigan normal admin workflowga moslashtiradi.
            # Auto-generate ID — client never supplies it
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
                total_questions=total_questions
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
            # Ichki xato matnini (SQL, yo'llar) mijozga qaytarmaymiz.
            logger.exception("Error creating listening exam: %s", e)
            raise HTTPException(400, detail="Listening imtihonini yaratib bo'lmadi. Ma'lumotlarni tekshiring.")

    async def create_part_mini_tests(self, exam_id: str) -> dict:
        """Har bir partdan mustaqil Listening mini-test yaratadi; takroriy chaqiruv dublikat yaratmaydi.

        - Savol/part raqamlari ASL holicha saqlanadi (instruction, gap-fill matni va
          xarita belgilari "(15-18)" kabi raqamlarga tayanadi).
        - audio_mode=PARTS: part audiosi nusxalanadi.
        - audio_mode=EXAM: umumiy audio_url va shu partning start/end segmenti saqlanadi.
        """
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

        # Audiosiz mini-test foydasiz — aniq xabar bilan to'xtatamiz.
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

        for position, part in enumerate(ordered_parts, start=1):
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
                selectinload(ListeningPart.options)
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

        # Create'dagi kabi: 6 part / 35 savol shabloni buzilgan holda saqlashga
        # yo'l qo'ymaymiz (aks holda test keyin lock/PDF/OMR'dan o'tmaydi).
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
            # ORM orqali o'chiramiz (bulk DELETE bola qatorlarni qoldirib ketardi)
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
        # Yagona qoida (exams/mock/usage.py): mock/OMR -> doim 409; natija/urinish -> faqat force (SUPER_ADMIN).
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
        """Javoblarni yuborish (eski kontrakt saqlangan).

        Body'dagi `user_answers` avval DB'ga upsert qilinadi, baholash SAQLANGAN
        javoblardan server tomonda bajariladi va natija DB'ga yoziladi
        (qarang: attempt_service). Takroriy yuborish mavjud natijani qaytaradi.
        """
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
        """
        Natija va savollar tahlilini qaytarish (Logdagi xatoni tuzatuvchi metod)
        """
        stmt = select(ListeningResult).where(
            ListeningResult.id == result_id, 
            ListeningResult.user_id == user_id
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
                u_ans = result_data.user_answers.get(str(q.question_number), "").strip().lower()
                c_ans_raw = q.correct_answer.strip().lower()
                valid_options = [a.strip() for a in c_ans_raw.split("/") if a.strip()]

                is_correct = bool(u_ans) and u_ans in valid_options
                
                review_data.append({
                    "question_id": q.id,
                    "question_number": q.question_number,
                    "user_answer": result_data.user_answers.get(str(q.question_number), ""),
                    "correct_answer": q.correct_answer,
                    "is_correct": is_correct,
                    "type": q.type
                })
                
        return {
            "summary": result_data,
            "review": review_data
        }

    # ================================================================
    #  MOCK EXAM INTEGRATION (INTERNAL)
    # ================================================================
    async def _update_mock_listening(
        self, exam_attempt_id: int, score: float, cefr_level: str, user_id: uuid.UUID,
        raw_score: float = 0.0,
    ):
        # IDOR FIX: mirrors reading_test._update_mock_reading — `exam_attempt_id`
        # is client-supplied (ListeningSubmission), so without the `user_id`
        # filter any authenticated user could overwrite another student's
        # Listening skill score (and potentially trigger finalization of that
        # student's attempt) by guessing/enumerating attempt IDs. Filtering on
        # MockSkillAttempt.user_id (set from the real owner at attempt
        # creation) restricts this to the caller's own skill row; a mismatch
        # now behaves like "not found", same as the existing `if skill:` path.
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
