"""Reading / Listening urinish (attempt) xizmati — javoblarni DB'da saqlash,
resume qilish va server tomonda baholab natijani yozish uchun YAGONA joy.

Muammo (ilgari):
  * Javoblar faqat submit paytidagi request body'da kelardi — brauzer yopilsa
    yoki tarmoq uzilsa hammasi yo'qolardi.
  * Mock'da `autosave` butun JSON'ni ustidan yozardi (race), `submit_skill`
    esa mavjud `ReadingResult`ni qidirib, topilmasa JIMGINA 0 ball yozardi.
  * Practice'da urinish (attempt) umuman yo'q edi.
  * Natija bir urinish uchun bir martadan ko'p yozilishi mumkin edi.

Endi:
  * Har bir javob `attempt_answers`da alohida qator (urinish + savol raqami
    bo'yicha UNIQUE, upsert => idempotent).
  * Baholash FAQAT saqlangan javoblardan, server-side to'g'ri javoblar bilan.
  * Finalizatsiya bitta tranzaksiyada: qator qulflanadi, natija yoziladi,
    urinish yopiladi. Takroriy finish mavjud natijani qaytaradi.
  * Natija DB'da UNIQUE (exam_attempt_id+test / practice_attempt_id) bilan
    himoyalangan.

Ball hisoblash (scaled/CEFR) mavjud servis metodlaridan olinadi
(`ReadingService._calculate_metrics`, `ListeningService._calculate_listening_metrics`).
Bu Rasch EMAS — mavjud konversiya jadvali.
"""
from __future__ import annotations

import unicodedata

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tests.attempts.timer import remaining_seconds, utc_now
from app.modules.exams.mock.models import (
    AttemptAnswer,
    AttemptStatus,
    SkillType,
)
from app.core.clock import utcnow

logger = logging.getLogger(__name__)

MAX_ANSWER_ITEMS = 10
MAX_ANSWER_LEN = 500


# ======================================================================
#  Skill konfiguratsiyasi (Reading va Listening o'z ma'lumotlaridan foydalanadi)
# ======================================================================
@dataclass
class SkillSpec:
    skill: SkillType
    result_model: Any
    result_test_col: str            # ReadingResult.test_id / ListeningResult.exam_id
    mock_test_attr: str             # MockExam.reading_id / listening_id
    load_test: Callable[[AsyncSession, str], Any]
    metrics: Callable[[int], tuple]
    questions_of: Callable[[Any], list]   # test -> [question ORM obj]
    answers_snapshot: Callable[[Dict[int, List[str]]], dict]
    set_result_extra: Callable[[Any, int, int, float], None]

    def result_test_attr(self):
        return getattr(self.result_model, self.result_test_col)


def spec_for(skill: SkillType) -> SkillSpec:
    # Lazy import — reading/listening servislari bu modulni import qiladi.
    if skill == SkillType.READING:
        from app.modules.tests.reading.models import ReadingResult
        from app.modules.tests.reading.services import ReadingService

        async def _load(db, test_id):
            return await ReadingService(db).get_test_by_id(test_id)

        def _extra(result, correct, total, pct):
            result.total_questions = total
            result.percentage = pct

        return SkillSpec(
            skill=skill,
            result_model=ReadingResult,
            result_test_col="test_id",
            mock_test_attr="reading_id",
            load_test=_load,
            metrics=ReadingService(None)._calculate_metrics,
            questions_of=lambda t: [q for p in t.parts for q in p.questions],
            answers_snapshot=lambda m: {str(n): list(a) for n, a in m.items()},
            set_result_extra=_extra,
        )
    if skill == SkillType.LISTENING:
        from app.modules.tests.listening.models import ListeningResult
        from app.modules.tests.listening.services import ListeningService

        async def _load(db, test_id):
            return await ListeningService(db).get_exam_by_id(test_id)

        def _extra(result, correct, total, pct):
            result.total_questions = total
            result.correct_answers = correct
            result.percentage = pct

        return SkillSpec(
            skill=skill,
            result_model=ListeningResult,
            result_test_col="exam_id",
            mock_test_attr="listening_id",
            load_test=_load,
            metrics=ListeningService(None)._calculate_listening_metrics,
            questions_of=lambda t: [q for p in t.parts for q in p.questions],
            answers_snapshot=lambda m: {str(n): (a[0] if a else "") for n, a in m.items()},
            set_result_extra=_extra,
        )
    raise HTTPException(400, "Faqat READING va LISTENING qo'llab-quvvatlanadi")


# ======================================================================
#  Normalizatsiya va baholash yordamchilari
# ======================================================================
def normalize_answer(value: Any) -> List[str]:
    """Klient javobini `List[str]` ga keltiradi (str -> [str], None -> [])."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if v is not None]
    else:
        items = [str(value).strip()]
    items = [i for i in items if i != ""]
    if len(items) > MAX_ANSWER_ITEMS:
        raise HTTPException(422, f"Bitta savolga {MAX_ANSWER_ITEMS} tadan ko'p javob yuborib bo'lmaydi")
    if any(len(i) > MAX_ANSWER_LEN for i in items):
        raise HTTPException(422, f"Javob {MAX_ANSWER_LEN} belgidan oshmasligi kerak")
    return items


_APOSTROPHES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "`": "'", "\u00b4": "'"})


def fold_text(value: Any) -> str:
    """Javobni solishtirish uchun normallashtiradi: aksent (CAFÉ -> cafe), turli apostroflar
    (O’CLOCK -> o'clock), katta-kichik harf va ortiqcha bo'shliqlar farq qilmaydi."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_APOSTROPHES).lower()
    return " ".join(text.split())


def correct_options(correct: Any) -> List[str]:
    """To'g'ri javob (list yoki 'a/b' string) -> normallashtirilgan alternativlar."""
    if isinstance(correct, (list, tuple)):
        raw = [str(o) for o in correct]
    else:
        raw = str(correct).split("/")
    return [fold_text(o) for o in raw if fold_text(o)]


def is_correct(user_answer: List[str], correct: Any) -> bool:
    # Bo'sh javob hech qachon to'g'ri emas (bo'sh kalit bo'lsa ham).
    u = fold_text(user_answer[0]) if user_answer else ""
    return bool(u) and u in correct_options(correct)


def resolve_question(questions: list, question_id: Optional[int], question_number: Optional[int]):
    """Savolni FAQAT shu testning savollari ichidan topadi (boshqa test -> 422)."""
    for q in questions:
        if question_number is not None and q.question_number == question_number:
            if question_id is not None and q.id != question_id:
                raise HTTPException(422, "question_id va question_number bir-biriga mos emas")
            return q
        if question_number is None and question_id is not None and q.id == question_id:
            return q
    raise HTTPException(422, f"Bu testda bunday savol yo'q (question_id={question_id}, question_number={question_number})")


class _Owner:
    """Javob egasi: mock skill-attempt YOKI practice attempt."""

    def __init__(self, *, mock_skill_attempt_id: Optional[int] = None, practice_attempt_id: Optional[int] = None):
        assert (mock_skill_attempt_id is None) != (practice_attempt_id is None)
        self.mock = mock_skill_attempt_id
        self.practice = practice_attempt_id

    @property
    def column(self):
        return AttemptAnswer.mock_skill_attempt_id if self.mock is not None else AttemptAnswer.practice_attempt_id

    @property
    def id(self) -> int:
        return self.mock if self.mock is not None else self.practice

    def kwargs(self) -> dict:
        return {"mock_skill_attempt_id": self.mock} if self.mock is not None else {"practice_attempt_id": self.practice}


async def _lock_row(db: AsyncSession, model, pk: int):
    """Qatorni tranzaksiya oxirigacha qulflaydi.

    Ma'lumotsiz (no-op) UPDATE: PostgreSQL'da qator qulfini oladi (parallel
    finish kutib turadi va keyin allaqachon yakunlangan holatni ko'radi);
    SQLite'da tranzaksiya darhol write-tranzaksiyaga aylanadi, shuning uchun
    "o'qib, keyin yozish" paytidagi snapshot to'qnashuvi bo'lmaydi.
    """
    await db.execute(update(model).where(model.id == pk).values(user_id=model.user_id))


async def upsert_answer(
    db: AsyncSession, owner: _Owner, question_number: int, question_id: Optional[int], answer: List[str]
) -> AttemptAnswer:
    """(urinish, savol raqami) bo'yicha bitta qator: bor bo'lsa UPDATE, yo'q bo'lsa INSERT."""

    async def _existing():
        return (await db.execute(
            select(AttemptAnswer).where(
                owner.column == owner.id, AttemptAnswer.question_number == question_number
            ).execution_options(populate_existing=True)
        )).scalar_one_or_none()

    row = await _existing()
    if row is None:
        try:
            async with db.begin_nested():
                row = AttemptAnswer(
                    question_number=question_number, question_id=question_id, answer=answer, **owner.kwargs()
                )
                db.add(row)
                await db.flush()
            await db.refresh(row)   # server-side created_at/updated_at ni yuklash
            return row
        except IntegrityError:
            # Parallel so'rov bizdan oldin yozib ulgurdi — uni yangilaymiz.
            row = await _existing()
            if row is None:
                raise
    if row.answer != answer or row.question_id != question_id:
        row.answer = answer
        row.question_id = question_id
        await db.flush()
        await db.refresh(row)
    return row


async def load_answers(db: AsyncSession, owner: _Owner) -> Dict[int, AttemptAnswer]:
    rows = (await db.execute(
        select(AttemptAnswer).where(owner.column == owner.id).execution_options(populate_existing=True)
    )).scalars().all()
    return {r.question_number: r for r in rows}


def is_correct_for_question(q, user_answer: List[str]) -> bool:
    """Savol turiga qarab to'g'ri javobni tekshiradi.

    MULTIPLE_SELECT: talabaning tanlovlari to'g'ri variantlar to'plamiga AYNAN teng bo'lishi kerak
    (bitta to'g'ri + bitta noto'g'ri tanlov bilan ball berilmaydi). Qolgan turlar: is_correct.
    """
    qtype = getattr(q, "type", None)
    qtype = getattr(qtype, "value", qtype)
    if qtype == "MULTIPLE_SELECT":
        expected = {fold_text(x) for x in (q.correct_answer or []) if x and fold_text(x)}
        given = {fold_text(x) for x in normalize_answer(user_answer) if x and fold_text(x)}
        return bool(expected) and given == expected
    return is_correct(user_answer, q.correct_answer)


def _score(spec: SkillSpec, questions: list, answers: Dict[int, List[str]]):
    correct = 0
    for q in questions:
        if is_correct_for_question(q, answers.get(q.question_number, [])):
            correct += 1
    total = len(questions)
    std, cefr = spec.metrics(correct)
    pct = round((correct / total) * 100, 2) if total > 0 else 0.0
    return correct, total, std, cefr, pct


def _utcnow_naive() -> datetime:
    # MockSkillAttempt.submitted_at — timezone'siz ustun (mavjud konvensiya).
    return utcnow()


# ======================================================================
#  PRACTICE
# ======================================================================


# ======================================================================
#  MOCK
# ======================================================================


# ======================================================================
#  LEGACY SUBMIT (body'da javoblar bilan) — saqlangan javoblarga ulanadi
# ======================================================================


# ======================================================================
#  STATE (resume) — javoblar bilan
# ======================================================================
def answer_item(row: AttemptAnswer) -> dict:
    ans = list(row.answer or [])
    return {
        "question_id": row.question_id,
        "question_number": row.question_number,
        "answers": ans,
        "answer": ans[0] if ans else "",
        "updated_at": row.updated_at or row.created_at,
    }


def _state_dict(*, attempt_id, test_id, skill, status, started_at, submitted_at, expires_at,
                last_saved_at, duration_seconds, total_questions, rows, result_id, exam_attempt_id,
                mode, part_number) -> dict:
    remaining = remaining_seconds(expires_at) if status in {"IN_PROGRESS", AttemptStatus.IN_PROGRESS.value} else 0 if expires_at else None
    items = [answer_item(r) for _, r in sorted(rows.items())]
    return {
        "attempt_id": attempt_id, "exam_attempt_id": exam_attempt_id, "mode": mode, "part_number": part_number,
        "test_id": test_id, "skill": skill.value, "status": status, "started_at": started_at,
        "submitted_at": submitted_at, "expires_at": expires_at, "server_time": utc_now(),
        "last_saved_at": last_saved_at,
        "duration_minutes": (duration_seconds // 60) if duration_seconds else None,
        "remaining_seconds": remaining, "total_questions": total_questions,
        "answered_count": sum(1 for i in items if i["answers"]), "result_id": result_id, "answers": items,
    }

