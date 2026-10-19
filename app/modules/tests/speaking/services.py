import asyncio
import logging
import os
import shutil
import tempfile
import uuid
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile
from app.modules.exams.paper.content_lock import ContentLockService
from app.modules.exams.paper.models import PaperModule
from app.modules.exams.paper.schemas import LockValidationError
from sqlalchemy import update, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.modules.auth.models import User
from app.core.id_generator import generate_speaking_id
from app.core.file_storage import generated_name, private_path, safe_suffix
from app.modules.tests.speaking.models import (
    SpeakingTest,
    SpeakingPart11,
    SpeakingPart12,
    SpeakingPart2,
    SpeakingPart3,
    SpeakingSubmission,
    SpeakingResult,
    SpeakingRecordingDraft,
)
from app.modules.tests.speaking.schemas import (
    SpeakingTestCreate,
    SpeakingTestUpdate,
    SpeakingResultCreate,
)
from app.modules.tests.speaking.speaking_config import (
    DURATION_GRACE_SECONDS,
    ENFORCE_RECORDING_LIMIT,
    SPEAKING_TIMINGS,
)
from app.core.clock import utcnow
from app.modules.tests.scoring_policy import writing_speaking_final

logger = logging.getLogger(__name__)

# Audio qayerga saqlanishi app/core/config.py (Settings) orqali boshqariladi:
#   SPEAKING_AUDIO_DIR / PUBLIC_BASE_URL / SPEAKING_ALLOWED_AUDIO_EXTENSIONS / SPEAKING_MAX_AUDIO_MB

# Chunk yuklashda ruxsat etilgan kengaytmalar (static'dan xizmat qilinadi, shuning
# uchun .html/.svg kabilar QAT'IYAN taqiqlangan).
ALLOWED_CHUNK_EXTENSIONS = {".webm", ".ogg", ".mp4", ".m4a", ".wav", ".mp3"}

_EBML_MAGIC = b"\x1a\x45\xdf\xa3"  # webm/matroska boshlanishi

_TEST_LOAD_OPTIONS = (
    selectinload(SpeakingTest.part_1_1),
    selectinload(SpeakingTest.part_1_2),
    selectinload(SpeakingTest.part_2),
    selectinload(SpeakingTest.part_3),
)

# Deterministik, barqaror ID'lar: mijoz bermasa shular beriladi; update'da mavjudi saqlanadi.
_P11_IDS = {"external_id": "part-1.1", "question_1_id": "part-1.1-q-1", "question_2_id": "part-1.1-q-2", "question_3_id": "part-1.1-q-3"}
_P12_IDS = {"external_id": "part-1.2", "question_describe_id": "part-1.2-q-1", "question_2_id": "part-1.2-q-2", "question_3_id": "part-1.2-q-3"}
_P2_IDS = {"external_id": "part-2", "question_1_id": "part-2-q-1", "question_2_id": "part-2-q-2", "question_3_id": "part-2-q-3"}
_P3_IDS = {"external_id": "part-3", "topic_id": "part-3-topic"}


def _new_uuid_defaults(defaults: dict) -> dict:
    """Yangi Speaking test uchun har bir part/savolga alohida UUID beradi."""
    return {key: str(uuid.uuid4()) for key in defaults}


def _with_ids(d: dict, defaults: dict) -> dict:
    """model_dump() berilmagan ID'lar uchun None qaytaradi — shuning uchun to'ldiramiz."""
    for k, v in defaults.items():
        if not d.get(k):
            d[k] = v
    return d


def _merge_ids(d: dict, current, defaults: dict) -> dict:
    """Yangilashda mijoz ID yubormasa, mavjud (yoki standart) ID saqlanadi."""
    for k, v in defaults.items():
        if not d.get(k):
            d[k] = (getattr(current, k, None) if current is not None else None) or v
    return d


def _upsert_part(test: SpeakingTest, attr: str, cls, dump: dict, ids: dict, timings: dict) -> None:
    """Mavjud qismni yangilaydi (vaqtlariga TEGMAYDI) yoki legacy test uchun yangi yaratadi."""
    current = getattr(test, attr)
    if current is None:
        setattr(test, attr, cls(**_with_ids(dump, ids), **timings))
        return
    for key, value in _merge_ids(dump, current, ids).items():
        setattr(current, key, value)


# ---------------------------------------------------------------------------
# Past darajali yordamchilar (sync — asyncio.to_thread ichida chaqiriladi)
# ---------------------------------------------------------------------------
def _write_bytes(path: str, content: bytes) -> None:
    with open(path, "wb") as f:
        f.write(content)


def _concat_bytes(paths: list[str], out_path: str) -> None:
    with open(out_path, "wb") as out:
        for p in paths:
            with open(p, "rb") as src:
                shutil.copyfileobj(src, out)


def _starts_new_media_session(head: bytes) -> bool:
    """Yangi MediaRecorder sessiyasi yangi header (webm EBML / mp4 ftyp) bilan boshlanadi."""
    return head[:4] == _EBML_MAGIC or head[4:8] == b"ftyp"


def _group_segments(paths: list[str]) -> list[list[str]]:
    """Chunklarni sessiyalarga ajratadi. Brauzer qulab qayta davom etganda bir nechta sessiya bo'ladi."""
    segments: list[list[str]] = []
    for p in paths:
        with open(p, "rb") as f:
            head = f.read(12)
        if not segments or _starts_new_media_session(head):
            segments.append([p])
        else:
            segments[-1].append(p)
    return segments


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _cleanup_chunks(paths: list[str]) -> None:
    parents = set()
    for p in paths:
        _safe_remove(p)
        parents.add(os.path.dirname(p))
    for d in parents:
        try:
            os.rmdir(d)  # faqat bo'sh bo'lsa
        except OSError:
            pass


def _chunk_path(entry: str) -> str:
    """chunk_urls elementidan diskdagi yo'lni tiklaydi (yangi: yo'l; legacy: URL)."""
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    if base and entry.startswith(base):
        entry = entry[len(base):]
    if os.path.exists(entry):
        return entry
    return entry.lstrip("/")


def _private_audio_url(submission_id: int) -> str:
    return f"/speaking-tests/submissions/{submission_id}/audio"


def _audio_suffix_from_magic(data: bytes, declared_ext: str) -> str | None:
    ext = (declared_ext or "").lower()
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE" and ext == ".wav":
        return ext
    if data.startswith(b"OggS") and ext == ".ogg":
        return ext
    if data.startswith(_EBML_MAGIC) and ext in {".webm", ".m4a", ".mp4"}:
        return ".webm"
    if len(data) >= 8 and data[4:8] == b"ftyp" and ext in {".m4a", ".mp4"}:
        return ext
    if data.startswith(b"ID3") and ext == ".mp3":
        return ext
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0 and ext == ".mp3":
        return ext
    return None


async def _probe_duration(path: str) -> float | None:
    """ffprobe bilan haqiqiy davomiylik. Yo'q bo'lsa yoki 'N/A' (MediaRecorder webm) bo'lsa None."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    try:
        return float(out.decode().strip())
    except ValueError:
        return None


class SpeakingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # ADMIN: SpeakingTest CRUD
    # ------------------------------------------------------------------
    async def create_test(self, data: SpeakingTestCreate) -> SpeakingTest:
        test_id = data.id or generate_speaking_id()
        if data.id and await self.db.get(SpeakingTest, test_id):
            raise HTTPException(409, "Bu test ID allaqachon mavjud")

        # Vaqtlar speaking_config dan; ID'lar JSON'da bo'lmasa standart.
        T = SPEAKING_TIMINGS
        def _mk(model, part, ids, key):
            if part is None:
                return None
            return model(**(_with_ids(part.model_dump(), _new_uuid_defaults(ids)) | T[key]))

        p11 = _mk(SpeakingPart11, data.part_1_1, _P11_IDS, "part_1_1")
        p12 = _mk(SpeakingPart12, data.part_1_2, _P12_IDS, "part_1_2")
        p2 = _mk(SpeakingPart2, data.part_2, _P2_IDS, "part_2")
        p3 = _mk(SpeakingPart3, data.part_3, _P3_IDS, "part_3")

        test = SpeakingTest(
            id=test_id,
            title=data.title,
            description=data.description,
            is_demo=data.is_demo,
            is_free=data.is_free,
            is_practice=data.is_practice,
            is_active=data.is_active,
            part_1_1=p11,
            part_1_2=p12,
            part_2=p2,
            part_3=p3,
        )
        self.db.add(test)
        await self.db.commit()
        return await self.get_test(test_id)

    async def list_tests(self) -> list[SpeakingTest]:
        result = await self.db.execute(
            select(SpeakingTest).order_by(SpeakingTest.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_test(self, test_id: str) -> SpeakingTest:
        stmt = (
            select(SpeakingTest)
            .options(*_TEST_LOAD_OPTIONS)
            .where(SpeakingTest.id == test_id)
        )
        test = (await self.db.execute(stmt)).scalar_one_or_none()
        if not test:
            raise HTTPException(404, "Speaking test topilmadi")
        return test

    async def generate_question_paper_pdf(self, test_id: str) -> bytes:
        """Savollarni javob kalitisiz, rasmiy namuna ko'rinishida (rasmlar bilan) PDF qiladi."""
        from app.modules.tests.speaking.question_paper_pdf import render_speaking_pdf

        test = await self.get_test(test_id)
        if not test.is_active or not test.is_practice:
            raise HTTPException(404, "Speaking practice testi topilmadi")
        # Rasm o'qish / PDF render sinxron va og'ir — event loop'ni bloklamaslik uchun thread'da.
        return await asyncio.to_thread(render_speaking_pdf, test)

    async def get_submission(self, submission_id: int) -> SpeakingSubmission | None:
        return await self.db.get(SpeakingSubmission, submission_id)

    @staticmethod
    def _attempt_key(submission: SpeakingSubmission) -> tuple:
        """Bitta urinishning barcha yozuvlari uchun umumiy kalit."""
        if submission.mock_skill_attempt_id is not None:
            return ("mock", submission.mock_skill_attempt_id)
        if submission.practice_attempt_id is not None:
            return ("practice", submission.practice_attempt_id)
        return ("single", submission.id)  # legacy: urinishga bog'lanmagan yozuv

    async def list_user_results(self, user_id) -> list:
        """Foydalanuvchining Speaking natijalari — URINISH boshiga BITTA.

        Natija urinishning barcha savol-yozuvlariga nusxalanadi, shuning uchun
        savol bo'yicha qaytarilsa bitta urinish ~10 ta bir xil natija bo'lib
        ko'rinardi. Obuna holati tarixiy natijalarni yashirmaydi.
        """
        stmt = (
            select(SpeakingResult, SpeakingSubmission)
            .join(SpeakingSubmission, SpeakingSubmission.id == SpeakingResult.submission_id)
            .where(SpeakingSubmission.user_id == user_id)
            .order_by(SpeakingResult.created_at.desc(), SpeakingResult.id.desc())
        )
        seen: set[tuple] = set()
        results: list = []
        for result, submission in (await self.db.execute(stmt)).all():
            key = self._attempt_key(submission)
            if key in seen:
                continue
            seen.add(key)
            results.append(result)
        return results

    async def _has_recordings(self, test_id: str) -> bool:
        """Testga audio yozuv yoki draft bog'langanmi (kontentni muzlatish uchun)."""
        for col in (SpeakingSubmission.speaking_test_id, SpeakingRecordingDraft.speaking_test_id):
            if (await self.db.execute(select(exists().where(col == test_id)))).scalar():
                return True
        return False

    async def _is_referenced(self, test_id: str) -> bool:
        """Testga yozuv YOKI imtihon urinishi bog'langanmi (o'chirishni bloklash uchun)."""
        from app.modules.exams.mock.models import MockSkillAttempt

        if await self._has_recordings(test_id):
            return True
        return bool(
            (await self.db.execute(
                select(exists().where(MockSkillAttempt.speaking_test_id == test_id))
            )).scalar()
        )

    async def update_test(self, test_id: str, data: SpeakingTestUpdate) -> SpeakingTest:
        await ContentLockService(self.db, PaperModule.SPEAKING, test_id).assert_unlocked("Speaking test")
        test = await self.get_test(test_id)

        parts_given = any(
            p is not None for p in (data.part_1_1, data.part_1_2, data.part_2, data.part_3)
        )
        if parts_given and await self._has_recordings(test_id):
            # Baholovchi talaba eshitganidan boshqa savol matnini ko'rmasligi uchun.
            raise HTTPException(
                409,
                "Bu testga audio yozuvlar bog'langan: savol/rasm kontentini o'zgartirib bo'lmaydi. "
                "Yangi test yarating (title/description/flaglarni o'zgartirish mumkin).",
            )

        top_level = data.model_dump(
            exclude_unset=True,
            exclude={"part_1_1", "part_1_2", "part_2", "part_3"},
        )
        for key, value in top_level.items():
            setattr(test, key, value)

        T = SPEAKING_TIMINGS
        if data.part_1_1 is not None:
            _upsert_part(test, "part_1_1", SpeakingPart11, data.part_1_1.model_dump(), _P11_IDS, T["part_1_1"])
        if data.part_1_2 is not None:
            _upsert_part(test, "part_1_2", SpeakingPart12, data.part_1_2.model_dump(), _P12_IDS, T["part_1_2"])
        if data.part_2 is not None:
            _upsert_part(test, "part_2", SpeakingPart2, data.part_2.model_dump(), _P2_IDS, T["part_2"])
        if data.part_3 is not None:
            _upsert_part(test, "part_3", SpeakingPart3, data.part_3.model_dump(), _P3_IDS, T["part_3"])

        await self.db.commit()
        return await self.get_test(test_id)

    async def lock_test(self, test_id: str, locked_by_user_id=None):
        """To'liq test: 4 qism ham to'ldirilgan bo'lishi shart; practice/part-only: kamida bitta qism."""
        test = await self.get_test(test_id)
        names = ("part_1_1", "part_1_2", "part_2", "part_3")
        present = [n for n in names if getattr(test, n, None) is not None]
        errors: list[LockValidationError] = []
        if not present:
            errors.append(LockValidationError(message="Testda hech qanday speaking qismi yo'q."))
        elif not test.is_practice:
            missing = [n.replace("part_", "Part ").replace("_", ".") for n in names if n not in present]
            if missing:
                errors.append(LockValidationError(
                    message=f"To'liq Speaking testda qismlar yetishmayapti: {missing} "
                            "(qismlarga ajratilgan test uchun is_practice=true qiling)."))
        snapshot = {"parts": present}
        version, errors = await ContentLockService(self.db, PaperModule.SPEAKING, test_id).lock(
            snapshot, len(present), errors, locked_by_user_id)
        if not errors:
            await self.db.commit()
        return version, errors

    async def delete_test(self, test_id: str) -> None:
        test = await self.db.get(SpeakingTest, test_id)
        if not test:
            raise HTTPException(404, "Speaking test topilmadi")
        if await self._is_referenced(test_id):
            raise HTTPException(
                409,
                "Bu test ishlatilgan (yozuv yoki imtihon urinishi bor), o'chirib bo'lmaydi. "
                "Uni faolsizlantiring: PUT {\"is_active\": false}.",
            )
        from app.modules.exams.mock.usage import ensure_not_in_mock_exam, purge_exam_versions
        await ensure_not_in_mock_exam(self.db, "speaking", test_id)
        await purge_exam_versions(self.db, PaperModule.SPEAKING, test_id)
        await self.db.delete(test)
        await self.db.commit()

    # ------------------------------------------------------------------
    # FOYDALANUVCHI: imtihonda tayinlangan (tasodifiy tanlangan) testni olish
    # ------------------------------------------------------------------
    async def get_assigned_test_for_skill_attempt(self, mock_skill_attempt) -> SpeakingTest:
        """`mock_skill_attempt.speaking_test_id` start_exam vaqtida tasodifiy tanlab qo'yilgan."""
        if not mock_skill_attempt.speaking_test_id:
            raise HTTPException(
                400,
                "Ushbu urinish uchun speaking test tayinlanmagan "
                "(bazada faol SpeakingTest topilmagan bo'lishi mumkin).",
            )
        return await self.get_test(mock_skill_attempt.speaking_test_id)

    # ------------------------------------------------------------------
    # Umumiy yordamchilar
    # ------------------------------------------------------------------
    @staticmethod
    def _valid_question_pairs(test) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for part in (test.part_1_1, test.part_1_2, test.part_2):
            if not part:
                continue
            for attr in ("question_1_id", "question_2_id", "question_3_id", "question_describe_id"):
                qid = getattr(part, attr, None)
                if qid:
                    pairs.add((part.external_id, qid))
        if test.part_3 and test.part_3.topic_id:
            pairs.add((test.part_3.external_id, test.part_3.topic_id))
        return pairs

    @staticmethod
    def _limit_for(test, part_external_id: str, question_external_id: str) -> int | None:
        """Savol uchun javob vaqti limiti (soniya).

        Part 2 da answer_seconds UCHTA savol uchun umumiy, shuning uchun u har
        bir savol yozuvi uchun eng yuqori chegara sifatida ishlatiladi.
        """
        for part in (test.part_1_1, test.part_1_2, test.part_2, test.part_3):
            if part and part.external_id == part_external_id:
                describe_id = getattr(part, "question_describe_id", None)
                if describe_id and question_external_id == describe_id:
                    return part.describe_answer_seconds
                return part.answer_seconds
        return None

    async def _owned_skill_attempt(self, user_id, skill_attempt_id: int, for_write: bool = False):
        from app.modules.exams.mock.models import MockSkillAttempt, SkillType

        row = (await self.db.execute(select(MockSkillAttempt).where(
            MockSkillAttempt.id == skill_attempt_id,
            MockSkillAttempt.user_id == user_id,
            MockSkillAttempt.skill == SkillType.SPEAKING,
        ))).scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Speaking urinish topilmadi")
        if for_write and row.is_checked:
            raise HTTPException(409, "Bu urinish allaqachon baholangan; yozuv qabul qilinmaydi")
        return row

    # ------------------------------------------------------------------
    # AUDIO YUKLASH (legacy to'g'ridan-to'g'ri yo'l; hozir router'da ochilmagan)
    # ------------------------------------------------------------------
    async def upload_submission(
        self,
        user_id: uuid.UUID,
        speaking_test_id: str,
        file: UploadFile,
        mock_skill_attempt_id: int | None = None,
        practice_attempt_id: int | None = None,
        question_external_id: str | None = None,
        part_external_id: str | None = None,
        duration_seconds: int | None = None,
    ) -> SpeakingSubmission:
        ext = os.path.splitext(file.filename or "")[1].lower() or ".mp3"
        allowed = {e.lower() for e in settings.SPEAKING_ALLOWED_AUDIO_EXTENSIONS}
        ext = safe_suffix(ext, allowed)
        max_bytes = settings.SPEAKING_MAX_AUDIO_MB * 1024 * 1024
        content = await file.read(max_bytes + 1)
        if not content:
            raise HTTPException(400, "Bo'sh audio fayl")
        if len(content) > max_bytes:
            raise HTTPException(413, f"Fayl hajmi {settings.SPEAKING_MAX_AUDIO_MB}MB dan oshmasligi kerak")
        actual_ext = _audio_suffix_from_magic(content[:64], ext)
        if actual_ext != ext:
            raise HTTPException(415, "Audio fayl tarkibi kengaytmaga mos kelmadi")

        storage_key = generated_name(ext)
        path = str(private_path("speaking", "submissions", storage_key))
        await asyncio.to_thread(_write_bytes, path, content)

        # IDOR himoyasi: mock_skill_attempt_id shu user'ga va SPEAKING turiga tegishli bo'lsin.
        if mock_skill_attempt_id is not None:
            from app.modules.exams.mock.models import (
                MockSkillAttempt as _OwnershipCheckAttempt,
                SkillType as _OwnershipSkillType,
            )

            owned = (await self.db.execute(
                select(_OwnershipCheckAttempt.id).where(
                    _OwnershipCheckAttempt.id == mock_skill_attempt_id,
                    _OwnershipCheckAttempt.user_id == user_id,
                    _OwnershipCheckAttempt.skill == _OwnershipSkillType.SPEAKING,
                )
            )).scalar_one_or_none()
            if owned is None:
                mock_skill_attempt_id = None

        if question_external_id or part_external_id:
            test = await self.get_test(speaking_test_id)
            if (part_external_id, question_external_id) not in self._valid_question_pairs(test):
                _safe_remove(path)
                raise HTTPException(400, "Speaking savol IDsi ushbu testga tegishli emas")

        submission = SpeakingSubmission(
            user_id=user_id,
            speaking_test_id=speaking_test_id,
            mock_skill_attempt_id=mock_skill_attempt_id,
            practice_attempt_id=practice_attempt_id,
            question_external_id=question_external_id,
            part_external_id=part_external_id,
            audio_url="",
            audio_storage_key=storage_key,
            duration_seconds=duration_seconds,
            status="pending",
        )

        if submission.mock_skill_attempt_id is None and submission.practice_attempt_id is None:
            auto = await self._find_open_skill_attempt(user_id, speaking_test_id)
            if auto:
                submission.mock_skill_attempt_id = auto.id

        self.db.add(submission)
        await self.db.flush()
        submission.audio_url = _private_audio_url(submission.id)
        try:
            await self.db.commit()
        except Exception:
            _safe_remove(path)
            raise
        await self.db.refresh(submission)
        return submission

    async def _find_open_skill_attempt(self, user_id, speaking_test_id: str):
        """Shu user + test uchun hali tekshirilmagan eng so'nggi SPEAKING skill attempt."""
        from app.modules.exams.mock.models import (
            MockExamAttempt,
            MockSkillAttempt,
            SkillType as MockSkillType,
        )

        stmt = (
            select(MockSkillAttempt)
            .join(MockExamAttempt, MockSkillAttempt.attempt_id == MockExamAttempt.id)
            .where(
                MockSkillAttempt.user_id == user_id,
                MockSkillAttempt.skill == MockSkillType.SPEAKING,
                MockSkillAttempt.speaking_test_id == speaking_test_id,
                MockSkillAttempt.is_checked == False,  # noqa: E712
            )
            .order_by(MockExamAttempt.started_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # BAHOLASH
    # ------------------------------------------------------------------
    async def list_pending(self, limit: int = 200, offset: int = 0) -> list[SpeakingSubmission]:
        """Return all pending Speaking submissions to authorized evaluators.

        Submission entitlements are checked when the candidate submits. Queue
        visibility must not depend on the candidate's current subscription tier.
        """
        result = await self.db.execute(
            select(SpeakingSubmission)
            .options(selectinload(SpeakingSubmission.user).selectinload(User.profile), selectinload(SpeakingSubmission.user).selectinload(User.contacts))
            .where(SpeakingSubmission.status == "pending")
            .order_by(SpeakingSubmission.created_at.asc(), SpeakingSubmission.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_pending_attempts(self, limit: int = 200, offset: int = 0) -> list[dict]:
        """Baholovchi navbati — URINISH bo'yicha guruhlangan (bitta urinish = bitta element).

        Har bir elementda urinishning barcha pending yozuvlari (tinglash uchun)
        va natija yuboriladigan `submission_id` bor.
        """
        rows = (await self.db.execute(
            select(SpeakingSubmission)
            .options(
                selectinload(SpeakingSubmission.user).selectinload(User.profile),
                selectinload(SpeakingSubmission.user).selectinload(User.contacts),
            )
            .where(SpeakingSubmission.status == "pending")
            .order_by(SpeakingSubmission.created_at.asc(), SpeakingSubmission.id.asc())
        )).scalars().all()

        groups: dict[tuple, list[SpeakingSubmission]] = {}
        for row in rows:
            groups.setdefault(self._attempt_key(row), []).append(row)

        items: list[dict] = []
        for members in groups.values():
            first = members[0]
            durations = [m.duration_seconds for m in members if m.duration_seconds is not None]
            items.append({
                "submission_id": first.id,
                "mock_skill_attempt_id": first.mock_skill_attempt_id,
                "practice_attempt_id": first.practice_attempt_id,
                "user_id": first.user_id,
                "full_name": first.full_name,
                "email": first.email,
                "phone": first.phone,
                "username": first.username,
                "speaking_test_id": first.speaking_test_id,
                "recordings_count": len(members),
                "total_duration_seconds": sum(durations) if durations else None,
                "recordings": members,
                "created_at": first.created_at,
            })
        return items[offset: offset + limit]

    async def submit_result(
        self,
        submission_id: int,
        evaluator_id: uuid.UUID,
        data: SpeakingResultCreate,
    ) -> SpeakingResult:
        """Natijani URINISH darajasida yozadi.

        Bir urinishda ~10 ta savol-yozuv bor. Baholovchi bir marta ball kiritadi;
        u shu urinishning barcha pending yozuvlariga nusxalanadi va
        manual_grade_skill FAQAT BIR MARTA chaqiriladi (aks holda Speaking bali
        oxirgi baholangan savolniki bo'lib qolardi).
        """
        target_submission = await self.get_submission(submission_id)
        if target_submission is None:
            raise HTTPException(404, "Speaking topshiriq topilmadi")
        # Do not re-check the candidate's current subscription at evaluation time.
        # Submission-time entitlement is already enforced; mock-exam eligibility
        # is separate from the candidate's later subscription state.

        # ATOMIK claim: "checked" holatiga faqat bitta baholash o'ta oladi (shartli UPDATE).
        # Ilgari SELECT ... FOR UPDATE edi — SQLite'da u ishlamasdi.
        claimed = await self.db.execute(
            update(SpeakingSubmission)
            .where(SpeakingSubmission.id == submission_id, SpeakingSubmission.status != "checked")
            .values(status="checked")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            exists = (await self.db.execute(
                select(SpeakingSubmission.id).where(SpeakingSubmission.id == submission_id)
            )).scalar_one_or_none()
            if exists is None:
                raise HTTPException(404, "Speaking topshiriq topilmadi")
            raise HTTPException(409, "Bu submission allaqachon baholangan")
        submission = (await self.db.execute(
            select(SpeakingSubmission)
            .where(SpeakingSubmission.id == submission_id)
            .execution_options(populate_existing=True)
        )).scalar_one()

        if submission.mock_skill_attempt_id is None:
            auto = await self._find_open_skill_attempt(submission.user_id, submission.speaking_test_id)
            if auto:
                submission.mock_skill_attempt_id = auto.id

        skill_attempt = None
        if submission.mock_skill_attempt_id:
            from app.modules.exams.mock.models import MockSkillAttempt

            skill_attempt = await self.db.get(MockSkillAttempt, submission.mock_skill_attempt_id)
            if skill_attempt and skill_attempt.is_checked:
                raise HTTPException(409, "Bu urinish allaqachon baholangan")

        payload = data.model_dump(exclude={"overall_raw"})
        payload["overall_band"] = writing_speaking_final(data.overall_raw)  # yakuniy ball 0–75
        result = SpeakingResult(submission_id=submission_id, evaluator_id=evaluator_id, **payload)
        self.db.add(result)
        submission.status = "checked"

        if skill_attempt:
            from app.modules.exams.mock.models import MockExamAttempt
            exam_attempt = await self.db.get(MockExamAttempt, skill_attempt.attempt_id)
            if exam_attempt and exam_attempt.is_finished:
                raise HTTPException(409, "Yakunlangan imtihon Speaking natijasini o'zgartirib bo'lmaydi")
            siblings = (await self.db.execute(
                select(SpeakingSubmission).where(
                    SpeakingSubmission.mock_skill_attempt_id == skill_attempt.id,
                    SpeakingSubmission.id != submission.id,
                    SpeakingSubmission.status == "pending",
                )
            )).scalars().all()
            for sib in siblings:
                sib.status = "checked"
                self.db.add(SpeakingResult(submission_id=sib.id, evaluator_id=evaluator_id, **payload))

            from app.modules.exams.mock.services import manual_grade_skill
            from app.modules.exams.mock.models import SkillType as MockSkillType

            await manual_grade_skill(
                self.db,
                skill_attempt.attempt_id,
                MockSkillType.SPEAKING,
                payload["overall_band"],
                raw_score=data.overall_raw,
            )
            # Baholovchi aniq cefr_level bergan bo'lsa, avtomatik qiymat ustidan yoziladi.
            if data.cefr_level:
                skill_attempt.cefr_level = data.cefr_level
        elif submission.practice_attempt_id is not None:
            # Practice urinish: baho butun urinishga BITTA — mock'dagi kabi.
            # Aks holda practice'da har bir savol-yozuv alohida baholanardi.
            from app.modules.exams.mock.models import PracticeAttempt, PracticeAttemptStatus
            from app.modules.tests.attempts.timer import transition_practice_attempt

            siblings = (await self.db.execute(
                select(SpeakingSubmission).where(
                    SpeakingSubmission.practice_attempt_id == submission.practice_attempt_id,
                    SpeakingSubmission.id != submission.id,
                    SpeakingSubmission.status == "pending",
                )
            )).scalars().all()
            for sib in siblings:
                sib.status = "checked"
                self.db.add(SpeakingResult(submission_id=sib.id, evaluator_id=evaluator_id, **payload))

            practice = await self.db.get(PracticeAttempt, submission.practice_attempt_id)
            if practice is not None and practice.status == PracticeAttemptStatus.IN_PROGRESS:
                # Baholangach urinish yopiladi: yangi yozuvlar eski (baholangan) urinishga qo'shilmasin.
                transition_practice_attempt(practice, PracticeAttemptStatus.SUBMITTED)
                practice.submitted_at = utcnow()
                practice.active_scope_key = None

        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(409, "Bu submission allaqachon baholangan")
        await self.db.refresh(result)
        return result

    # ------------------------------------------------------------------
    # RECORDING DRAFT (chunk yuklash / tiklash / yakunlash)
    # ------------------------------------------------------------------
    async def _get_draft(self, skill_attempt_id: int, part_external_id: str,
                         question_external_id: str, lock: bool = False):
        stmt = select(SpeakingRecordingDraft).where(
            SpeakingRecordingDraft.mock_skill_attempt_id == skill_attempt_id,
            SpeakingRecordingDraft.part_external_id == part_external_id,
            SpeakingRecordingDraft.question_external_id == question_external_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def save_recording_chunk(
        self, user_id, skill_attempt_id: int, speaking_test_id: str,
        part_external_id: str, question_external_id: str,
        file: UploadFile, chunk_index: int, duration_seconds: int | None = None,
    ):
        skill_attempt = await self._owned_skill_attempt(user_id, skill_attempt_id, for_write=True)
        if skill_attempt.speaking_test_id != speaking_test_id:
            raise HTTPException(400, "Speaking test ushbu urinishga biriktirilmagan")
        test = await self.get_test(speaking_test_id)
        if (part_external_id, question_external_id) not in self._valid_question_pairs(test):
            raise HTTPException(400, "Speaking savol IDsi ushbu testga tegishli emas")
        if chunk_index < 0:
            raise HTTPException(400, "chunk_index noto'g'ri")

        ext = os.path.splitext(file.filename or "")[1].lower() or ".webm"
        ext = safe_suffix(ext, ALLOWED_CHUNK_EXTENSIONS)
        max_bytes = settings.SPEAKING_MAX_AUDIO_MB * 1024 * 1024
        content = await file.read(max_bytes + 1)
        if not content:
            raise HTTPException(400, "Bo'sh audio chunk")
        if len(content) > max_bytes:
            raise HTTPException(413, "Audio chunk hajmi juda katta")
        if _audio_suffix_from_magic(content[:64], ext) != ext:
            raise HTTPException(415, "Audio chunk tarkibi kengaytmaga mos kelmadi")

        draft = await self._get_draft(skill_attempt_id, part_external_id, question_external_id, lock=True)
        if draft is None:
            draft = SpeakingRecordingDraft(
                user_id=user_id, mock_skill_attempt_id=skill_attempt_id,
                speaking_test_id=speaking_test_id, part_external_id=part_external_id,
                question_external_id=question_external_id, chunk_urls=[], next_chunk_index=0,
            )
            self.db.add(draft)
            try:
                await self.db.flush()
            except IntegrityError:
                # Parallel so'rov draftni ulgurib yaratgan.
                await self.db.rollback()
                draft = await self._get_draft(
                    skill_attempt_id, part_external_id, question_external_id, lock=True
                )
                if draft is None:
                    raise HTTPException(409, "Draft yaratishda ziddiyat, qayta urinib ko'ring")

        if draft.user_id != user_id:
            raise HTTPException(404, "Speaking recording draft topilmadi")
        if draft.status == "finalized":
            raise HTTPException(409, "Bu savol uchun recording allaqachon yakunlangan")
        if draft.total_bytes + len(content) > max_bytes:
            raise HTTPException(413, "Recording umumiy hajmi limitdan oshdi")
        if chunk_index != draft.next_chunk_index:
            raise HTTPException(409, f"Kutilayotgan chunk_index: {draft.next_chunk_index}")

        # part ham yo'lga kiradi: turli qismlarda bir xil savol ID bo'lsa fayllar to'qnashmasin.
        if not all(part and Path(part).name == part and part not in {".", ".."} for part in (part_external_id, question_external_id)):
            raise HTTPException(400, "Speaking savol identifikatori noto'g'ri")
        audio_dir = private_path("speaking", "drafts", str(skill_attempt_id), part_external_id, question_external_id)
        await asyncio.to_thread(os.makedirs, audio_dir, exist_ok=True)
        path = str(audio_dir / f"chunk_{chunk_index:06d}{ext}")
        await asyncio.to_thread(_write_bytes, path, content)

        urls = list(draft.chunk_urls or [])
        urls.append(path)  # diskdagi yo'l saqlanadi (URL emas)
        draft.chunk_urls = urls
        draft.next_chunk_index += 1
        draft.total_bytes += len(content)
        if duration_seconds is not None:
            draft.duration_seconds = duration_seconds
        draft.updated_at = utcnow()
        await self.db.commit()
        await self.db.refresh(draft)
        return draft

    async def get_recording_draft(self, user_id, skill_attempt_id: int, part_external_id: str, question_external_id: str):
        await self._owned_skill_attempt(user_id, skill_attempt_id)
        row = (await self.db.execute(select(SpeakingRecordingDraft).where(
            SpeakingRecordingDraft.user_id == user_id,
            SpeakingRecordingDraft.mock_skill_attempt_id == skill_attempt_id,
            SpeakingRecordingDraft.part_external_id == part_external_id,
            SpeakingRecordingDraft.question_external_id == question_external_id,
        ))).scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Speaking recording draft topilmadi")
        return row

    async def _merge_segments(self, segments: list[list[str]], final_dir: str, base: str) -> str:
        """Bir nechta MediaRecorder sessiyasini ffmpeg bilan bitta faylga birlashtiradi.

        Baytma-bayt yopishtirish bu holatda FAQAT birinchi sessiyani ijro etadigan
        buzuq fayl beradi, shuning uchun re-encode (opus/webm) qilinadi.
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(
                503,
                "Yozuv bir necha sessiyadan iborat; birlashtirish uchun serverda ffmpeg kerak. "
                "Chunklar saqlangan — ffmpeg o'rnatilgach finalize'ni qayta chaqiring.",
            )
        tmp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix=".merge_", dir=final_dir)
        out_path = os.path.join(final_dir, base + ".webm")
        try:
            seg_files = []
            for i, seg in enumerate(segments):
                ext = os.path.splitext(seg[0])[1].lower() or ".webm"
                seg_path = os.path.join(tmp_dir, f"seg_{i:03d}{ext}")
                await asyncio.to_thread(_concat_bytes, seg, seg_path)
                seg_files.append(seg_path)

            list_path = os.path.join(tmp_dir, "list.txt")
            lines = "".join(
                "file '{}'\n".format(os.path.abspath(p).replace("'", "'\\''")) for p in seg_files
            )
            await asyncio.to_thread(_write_bytes, list_path, lines.encode("utf-8"))

            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                "-i", list_path, "-vn", "-c:a", "libopus", "-b:a", "48k", out_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                raise HTTPException(504, "Audio birlashtirish vaqti tugadi; qayta urinib ko'ring")
            if proc.returncode != 0:
                logger.error("ffmpeg concat xatosi: %s", err.decode(errors="replace"))
                _safe_remove(out_path)
                raise HTTPException(500, "Audio birlashtirishda xatolik")
            return out_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def finalize_recording_draft(self, user_id, draft_id: int):
        # ATOMIK claim: parallel finalize ikkita submission yaratmasin. Faqat "recording"
        # holatidagi draft "finalizing" ga o'tadi; tranzaksiya xato bilan tugasa, holat qaytadi.
        claimed = await self.db.execute(
            update(SpeakingRecordingDraft)
            .where(
                SpeakingRecordingDraft.id == draft_id,
                SpeakingRecordingDraft.user_id == user_id,
                SpeakingRecordingDraft.status == "recording",
            )
            .values(status="finalizing")
            .execution_options(synchronize_session=False)
        )
        draft = (await self.db.execute(
            select(SpeakingRecordingDraft)
            .where(SpeakingRecordingDraft.id == draft_id, SpeakingRecordingDraft.user_id == user_id)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if not draft:
            raise HTTPException(404, "Recording draft topilmadi")
        if claimed.rowcount != 1:
            if draft.status == "finalized":
                return await self.get_submission_by_draft(draft)
            raise HTTPException(409, "Yakunlash allaqachon davom etmoqda")
        if not draft.chunk_urls:
            raise HTTPException(400, "Finalize qilish uchun audio chunklar yo'q")

        paths = [_chunk_path(e) for e in draft.chunk_urls]
        if any(not os.path.exists(p) for p in paths):
            raise HTTPException(409, "Audio chunk topilmadi; recordingni qayta davom ettiring")

        final_dir = str(private_path("speaking", "submissions"))
        await asyncio.to_thread(os.makedirs, final_dir, exist_ok=True)
        base = secrets.token_hex(16)
        segments = await asyncio.to_thread(_group_segments, paths)
        if len(segments) == 1:
            ext = os.path.splitext(paths[0])[1].lower() or ".webm"
            final_path = os.path.join(final_dir, base + ext)
            await asyncio.to_thread(_concat_bytes, paths, final_path)
        else:
            final_path = await self._merge_segments(segments, final_dir, base)

        # Davomiylikni serverda tekshirish (ffprobe bo'lsa haqiqiy qiymat, bo'lmasa klient aytgani).
        measured = await _probe_duration(final_path)
        duration = round(measured) if measured is not None else draft.duration_seconds
        if ENFORCE_RECORDING_LIMIT and duration is not None:
            test = await self.get_test(draft.speaking_test_id)
            limit = self._limit_for(test, draft.part_external_id, draft.question_external_id)
            if limit and duration > limit + DURATION_GRACE_SECONDS:
                _safe_remove(final_path)
                raise HTTPException(
                    422,
                    f"Yozuv davomiyligi ({duration}s) ruxsat etilgan limitdan ({limit}s) oshdi",
                )

        storage_key = os.path.basename(final_path)
        submission = SpeakingSubmission(
            user_id=user_id, mock_skill_attempt_id=draft.mock_skill_attempt_id,
            speaking_test_id=draft.speaking_test_id, question_external_id=draft.question_external_id,
            part_external_id=draft.part_external_id, audio_url="", audio_storage_key=storage_key,
            duration_seconds=duration, status="pending",
        )
        self.db.add(submission)
        await self.db.flush()
        submission.audio_url = _private_audio_url(submission.id)
        draft.status = "finalized"
        draft.chunk_urls = []
        try:
            await self.db.commit()
        except IntegrityError:
            # Unique constraint: submission allaqachon bor — dublikat fayl va draftni tozalaymiz.
            await self.db.rollback()
            _safe_remove(final_path)
            draft = await self.db.get(SpeakingRecordingDraft, draft_id)
            return await self.get_submission_by_draft(draft)
        await self.db.refresh(submission)
        await asyncio.to_thread(_cleanup_chunks, paths)
        return submission

    async def get_submission_by_draft(self, draft):
        row = (await self.db.execute(select(SpeakingSubmission).where(
            SpeakingSubmission.mock_skill_attempt_id == draft.mock_skill_attempt_id,
            SpeakingSubmission.part_external_id == draft.part_external_id,
            SpeakingSubmission.question_external_id == draft.question_external_id,
        ).order_by(SpeakingSubmission.id.desc()).limit(1))).scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Final speaking submission topilmadi")
        return row  