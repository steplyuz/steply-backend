from app.core.file_storage import PRIVATE_ROOT as _PRIVATE_ROOT
"""
exams_auth/router.py
======================
Exams platformasi (examx.steply.uz) uchun MUSTAQIL endpointlar:

  - /api/v1/exams-auth/*  — login (MK ID + ixtiyoriy ikkinchi omil: telefon oxirgi 4 raqami), me, logout, refresh
  - /api/v1/exams/*       — faqat shu studentga tegishli imtihonlar
                             ro'yxati va imtihon topshirish oqimi

Ikkalasi ham `get_current_exams_session` orqali himoyalangan — asosiy
`get_current_user` (Steply) bu yerda IShLATILMAYDI.

Imtihon topshirish (start/status/submit/finish/result) logikasi
`mock_test.services` dagi (endi egalik/enrollment tekshiruvi bilan
mustahkamlangan) funksiyalarni chaqiradi — ikki marta yozilmaydi, faqat
ikkinchi, mustaqil "eshik" (auth) qo'shiladi.
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import FileResponse
from app.core.file_storage import ensure_private_file
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.core.rate_limit import limiter
from app.modules.exams.mock import services as mock_services
from app.modules.exams.mock.models import MockExam, MockExamResult, MockSkillAttempt, RegistrationStatus
from app.modules.exams.mock.schemas import (
    MockExamResultResponse,
    MockExamStartResponse,
    MockSkillStatusResponse,
    MockSkillAutosaveRequest,
    SkillType,
)
from app.modules.tests.reading.schemas import ReadingResultDetailResponse, ReadingSubmitRequest, ReadingTestResponse
from app.modules.tests.reading.services import ReadingService
from app.modules.tests.listening.schemas import ListeningExamResponse, ListeningResultDetailResponse, ListeningSubmission
from app.modules.tests.listening.services import ListeningService
from app.modules.tests.writing.schemas import WritingExamRead, WritingResultRead, WritingSubmitRequest
from app.modules.tests.writing.services import WritingService
from app.modules.tests.speaking.schemas import (
    SpeakingAssignedTestResponse,
    SpeakingSubmissionResponse,
)
from app.modules.tests.speaking.services import SpeakingService

from .dependencies import (
    EXAMS_ACCESS_COOKIE,
    EXAMS_REFRESH_COOKIE,
    ExamsSession,
    get_current_exams_session,
)
from .schemas import (
    EnrolledExamResponse,
    ExamsStatusResponse,
    ExamsStudentResponse,
    ExamsTokenResponse,
    ExamIdLoginRequest,
    ExamIdLoginResponse,
)
from app.modules.tests.attempts.schemas import AnswerSaveRequest, AttemptStateResponse, SavedAnswer
from .security import EXAMS_ACCESS_TOKEN_MINUTES, EXAMS_REFRESH_TOKEN_DAYS
from .service import ExamsAuthService
from app.core.clock import utcnow
from app.modules.exams import attempts as _exa

router = APIRouter(prefix="/exams-auth", tags=["Exams Auth"])
exams_router = APIRouter(prefix="/exams", tags=["Exams"])


def get_service(db: AsyncSession = Depends(get_db)) -> ExamsAuthService:
    return ExamsAuthService(db)


# =====================================================
# COOKIES — asosiy auth/router.py dagi cookie nomlaridan ATAYLAB boshqa
# ("exams_access_token"/"exams_refresh_token"), shunda ikkalasi bitta
# brauzerda/domenda ham bir-birini bosib qolmaydi.
# =====================================================


def _set_exams_cookies(response: Response, token: ExamsTokenResponse) -> None:
    cookie_kwargs = dict(
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    if settings.COOKIE_DOMAIN:
        cookie_kwargs["domain"] = settings.COOKIE_DOMAIN

    response.set_cookie(
        key=EXAMS_ACCESS_COOKIE,
        value=token.access_token,
        max_age=EXAMS_ACCESS_TOKEN_MINUTES * 60,
        **cookie_kwargs,
    )
    response.set_cookie(
        key=EXAMS_REFRESH_COOKIE,
        value=token.refresh_token,
        max_age=EXAMS_REFRESH_TOKEN_DAYS * 86400,
        **cookie_kwargs,
    )


def _clear_exams_cookies(response: Response) -> None:
    kwargs = dict(path="/")
    if settings.COOKIE_DOMAIN:
        kwargs["domain"] = settings.COOKIE_DOMAIN
    response.delete_cookie(EXAMS_ACCESS_COOKIE, **kwargs)
    response.delete_cookie(EXAMS_REFRESH_COOKIE, **kwargs)


# =====================================================
# AUTH ENDPOINTS
# =====================================================


@router.post("/login", response_model=ExamIdLoginResponse)
@limiter.limit("10/minute")
async def exams_login(
    request: Request,
    response: Response,
    data: ExamIdLoginRequest,
    service: ExamsAuthService = Depends(get_service),
):
    """Examga kirish: Student ID (MK ID) yoki random Exam ID."""
    if data.student_id:
        result = await service.login_by_student_id(data.student_id, request, data.phone_last4)
    else:
        result = await service.login_by_exam_id(data.exam_id, request)
    if result.token:
        _set_exams_cookies(response, result.token)
    return result


@router.get("/me", response_model=ExamsStudentResponse)
async def exams_me(
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: ExamsAuthService = Depends(get_service),
):
    # MK ID endi profildan emas, joriy seansning registrationidan keladi.
    return await service.get_me(exams_session.user.id, exams_session.registration_id)


@router.post("/refresh", response_model=ExamsStatusResponse)
async def exams_refresh(
    request: Request,
    response: Response,
    service: ExamsAuthService = Depends(get_service),
):
    refresh_token = request.cookies.get(EXAMS_REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(401, "Exams refresh token topilmadi")
    tokens = await service.refresh(refresh_token, request)
    _set_exams_cookies(response, tokens)
    return ExamsStatusResponse(status="success", message="Sessiya yangilandi")


@router.post("/logout", response_model=ExamsStatusResponse)
async def exams_logout(
    request: Request,
    response: Response,
    service: ExamsAuthService = Depends(get_service),
):
    """Faqat Exams sessiyasini tugatadi — asosiy Steply login holatiga tegmaydi."""
    refresh_token = request.cookies.get(EXAMS_REFRESH_COOKIE)
    if refresh_token:
        await service.logout(refresh_token)
    _clear_exams_cookies(response)
    return ExamsStatusResponse(status="success", message="Exams sessiyasidan chiqildi")


# =====================================================
# EXAM LIST + TAKING FLOW (Exams-auth bilan himoyalangan)
# =====================================================


@exams_router.get("", response_model=list[EnrolledExamResponse])
async def list_my_exams(
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    """Joriy MK ID (seans) bog'langan imtihon."""
    service = ExamsAuthService(db)
    return await service.list_enrolled_exams(
        exams_session.user.id, exams_session.registration_id
    )

@exams_router.get("/my-skills", summary="Faqat men uchun biriktirilgan 4 skill test IDlari")
async def get_my_exam_skills(
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    """
    Exam ID orqali kirgan talabaning faqat O'Z imtihoniga biriktirilgan
    4 ta skill (Reading/Listening/Writing/Speaking) uchun test IDlarini qaytaradi.
    Boshqa imtihonlarning test IDlari ko'rsatilmaydi.
    """
    service = ExamsAuthService(db)
    registration = await service._load_registration(exams_session.registration_id)
    if registration.user_id != exams_session.user.id:
        raise HTTPException(403, "Exams seansi yaroqsiz")
    
    exam = await db.get(MockExam, registration.mock_exam_id)
    if not exam or not exam.is_active:
        raise HTTPException(404, "Imtihon topilmadi yoki faol emas")
    
    # Attempt mavjudligi
    from app.modules.exams.mock.models import MockExamAttempt, MockSkillAttempt
    from sqlalchemy import select as _sel
    attempt = (await db.execute(
        _sel(MockExamAttempt)
        .where(
            MockExamAttempt.user_id == exams_session.user.id,
            MockExamAttempt.mock_exam_id == exam.id,
            MockExamAttempt.registration_id == registration.id,
        )
        .order_by(MockExamAttempt.started_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    
    skill_statuses = {}
    if attempt:
        skill_rows = (await db.execute(
            _sel(MockSkillAttempt).where(MockSkillAttempt.attempt_id == attempt.id)
        )).scalars().all()
        skill_statuses = {
            s.skill.value: {
                "is_submitted": s.submitted_at is not None,
                "is_checked": s.is_checked,
                "submitted_at": s.submitted_at,
            }
            for s in skill_rows
        }
    
    return {
        "exam_id": exam.id,
        "exam_title": exam.title,
        "registration_status": registration.status.value,
        "attempt_id": attempt.id if attempt else None,
        "skills": {
            "reading": {
                "test_id": exam.reading_id,
                **skill_statuses.get("READING", {"is_submitted": False, "is_checked": False, "submitted_at": None}),
            },
            "listening": {
                "test_id": exam.listening_id,
                **skill_statuses.get("LISTENING", {"is_submitted": False, "is_checked": False, "submitted_at": None}),
            },
            "writing": {
                "test_id": exam.writing_id,
                **skill_statuses.get("WRITING", {"is_submitted": False, "is_checked": False, "submitted_at": None}),
            },
            "speaking": {
                "test_id": exam.speaking_id,
                **skill_statuses.get("SPEAKING", {"is_submitted": False, "is_checked": False, "submitted_at": None}),
            },
        },
    }



@exams_router.post("/{exam_id}/start", response_model=MockExamStartResponse)
async def start_my_exam(
    exam_id: str,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    service = ExamsAuthService(db)
    registration = await service.require_enrollment(
        exams_session.user.id, exams_session.registration_id, exam_id
    )
    attempt = await mock_services.start_exam(
        db, exams_session.user.id, exam_id, registration_id=registration.id
    )
    from app.modules.tests.attempts.timer import remaining_seconds, utc_now
    return {
        "attempt_id": attempt.id,
        "mock_exam_id": attempt.mock_exam_id,
        "started_at": attempt.started_at,
        "expires_at": attempt.expires_at,
        "server_time": utc_now(),
        "remaining_seconds": remaining_seconds(attempt.expires_at),
    }


@exams_router.get("/attempts/{attempt_id}/status", response_model=list[MockSkillStatusResponse])
async def my_exam_attempt_status(
    attempt_id: int,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    await _require_session_attempt(db, attempt_id, exams_session)
    return await mock_services.get_attempt_status_service(db, attempt_id, exams_session.user.id)


@exams_router.patch("/attempts/{attempt_id}/progress/{skill}")
async def autosave_my_exam_skill(
    attempt_id: int,
    skill: SkillType,
    data: MockSkillAutosaveRequest,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    """Exam crash/reconnect uchun skill progress va draftni saqlaydi."""
    await _require_session_attempt(db, attempt_id, exams_session)
    return await mock_services.autosave_skill(
        db, attempt_id, skill, exams_session.user.id,
        current_part=data.current_part,
        current_question=data.current_question,
        draft=data.draft,
        user_answers=data.user_answers,
    )


@exams_router.post("/attempts/{attempt_id}/submit/{skill}")
async def submit_my_exam_skill(
    attempt_id: int,
    skill: SkillType,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    await _require_session_attempt(db, attempt_id, exams_session)
    result = await mock_services.submit_skill(db, attempt_id, skill, exams_session.user.id)
    # 4 skill ham topshirilgan bo'lsa — sessiya avtomatik yopiladi
    await ExamsAuthService(db).end_session_if_all_submitted(attempt_id)
    return result


async def _result_response(db: AsyncSession, result: MockExamResult) -> MockExamResultResponse:
    from app.modules.exams.mock.finalize import _skill_state
    _rows, _by_skill, states, _scores = await _skill_state(db, result.attempt_id)
    payload = MockExamResultResponse.model_validate(result)
    return payload.model_copy(update={
        "overall_status": "FINALIZED" if result.status == "FINALIZED" else "PENDING",
        "skill_statuses": states,
    })


@exams_router.post("/attempts/{attempt_id}/finish", response_model=MockExamResultResponse)
async def finish_my_exam(
    attempt_id: int,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    await _require_session_attempt(db, attempt_id, exams_session)
    result = await mock_services.finish_exam_service(db, attempt_id, exams_session.user.id, allow_pending=True)
    return await _result_response(db, result)


@exams_router.get("/attempts/{attempt_id}/result", response_model=MockExamResultResponse)
async def my_exam_result(
    attempt_id: int,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    return await mock_services.get_mock_result_service(db, attempt_id, exams_session.user.id)



# =====================================================
# READING / LISTENING: javoblarni saqlash, resume, natija
# =====================================================
def _rl_spec(skill: SkillType):
    from app.modules.tests.attempts import common as at
    if skill not in (SkillType.READING, SkillType.LISTENING):
        raise HTTPException(400, "Faqat READING va LISTENING uchun")
    return at, at.spec_for(skill)


async def _require_session_attempt(
    db: AsyncSession, attempt_id: int, exams_session: ExamsSession
):
    """Bind an attempt to the exact Exams registration in the access token.

    User ownership alone is insufficient because one student can legitimately
    have multiple exam registrations/attempts.
    """
    from app.modules.exams.mock.models import MockExamAttempt
    attempt = await db.get(MockExamAttempt, attempt_id)
    if not attempt or attempt.user_id != exams_session.user.id:
        raise HTTPException(404, "Imtihon urinish topilmadi")
    if exams_session.registration_id is not None and attempt.registration_id != exams_session.registration_id:
        raise HTTPException(404, "Imtihon urinish topilmadi")
    return attempt


@exams_router.get("/attempts/{attempt_id}/answers/{skill}", response_model=AttemptStateResponse)
async def my_saved_answers(
    attempt_id: int,
    skill: SkillType,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    """Resume: shu bo'lim uchun serverda saqlangan javoblar + holat."""
    at, spec = _rl_spec(skill)
    await _require_session_attempt(db, attempt_id, exams_session)
    return await _exa.mock_state(db, spec, exams_session.user.id, attempt_id)


@exams_router.put("/attempts/{attempt_id}/answers/{skill}", response_model=SavedAnswer)
async def save_my_answer(
    attempt_id: int,
    skill: SkillType,
    data: AnswerSaveRequest,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    """Bitta javobni saqlaydi/yangilaydi (idempotent). Topshirilgan bo'limda 409."""
    at, spec = _rl_spec(skill)
    await _require_session_attempt(db, attempt_id, exams_session)
    try:
        row = await _exa.save_mock_answer(
            db, spec, exams_session.user.id, attempt_id,
            data.question_id, data.question_number, data.normalized(),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return at.answer_item(row)


@exams_router.get("/attempts/{attempt_id}/results/{skill}")
async def my_skill_result(
    attempt_id: int,
    skill: SkillType,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    """Return the persisted result for any of the four official skills."""
    user_id = exams_session.user.id
    attempt = await _require_session_attempt(db, attempt_id, exams_session)

    if skill in (SkillType.READING, SkillType.LISTENING):
        at, spec = _rl_spec(skill)
        result = await _exa.get_mock_result(db, spec, user_id, attempt_id)
        if skill == SkillType.READING:
            return await ReadingService(db).get_result_with_review(result.id, user_id)
        return ListeningResultDetailResponse.model_validate(
            await ListeningService(db).get_result_with_review(result.id, user_id)
        )

    if skill == SkillType.WRITING:
        from app.modules.exams.mock.models import MockExam
        from app.modules.tests.writing.models import WritingResult
        exam = await db.get(MockExam, attempt.mock_exam_id)
        result = (await db.execute(
            select(WritingResult).where(
                WritingResult.exam_attempt_id == attempt_id,
                WritingResult.user_id == user_id,
                WritingResult.exam_id == exam.writing_id,
            ).order_by(WritingResult.created_at.desc()).limit(1)
        )).scalar_one_or_none() if exam else None
        return {
            "skill": skill.value,
            "status": "COMPLETED" if result and result.is_finalized else ("CHECKING" if result else "NOT_STARTED"),
            "result": result,
        }

    from app.modules.tests.speaking.models import SpeakingResult, SpeakingSubmission
    skill_row = (await db.execute(
        select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == attempt_id,
            MockSkillAttempt.skill == SkillType.SPEAKING,
            MockSkillAttempt.user_id == user_id,
        )
    )).scalar_one_or_none()
    skill_attempt_id = skill_row.id if skill_row else -1
    rows = (await db.execute(
        select(SpeakingResult, SpeakingSubmission)
        .join(SpeakingSubmission, SpeakingResult.submission_id == SpeakingSubmission.id)
        .where(SpeakingSubmission.mock_skill_attempt_id == skill_attempt_id)
        .order_by(SpeakingSubmission.created_at.asc())
    )).all()
    return {
        "skill": skill.value,
        "status": "COMPLETED" if skill_row and skill_row.is_checked else ("CHECKING" if skill_row and skill_row.submitted_at else "NOT_STARTED"),
        # Natija urinish darajasida: barcha savol-yozuvlarga bir xil nusxalanadi,
        # shuning uchun nomzodga BITTA umumiy natija qaytariladi.
        "results": [rows[0][0]] if rows else [],
    }


async def _require_assigned_skill(
    service: ExamsAuthService,
    db: AsyncSession,
    exams_session: ExamsSession,
    skill: SkillType,
    submitted_test_id: str,
    allow_submitted: bool = False,
):
    registration = await service._load_registration(exams_session.registration_id)
    if registration.user_id != exams_session.user.id:
        raise HTTPException(403, "Exams seansi yaroqsiz")
    if registration.status not in {RegistrationStatus.CHECKED_IN, RegistrationStatus.IN_PROGRESS}:
        raise HTTPException(403, "Imtihon uchun check-in tasdiqlanmagan")
    exam = await db.get(MockExam, registration.mock_exam_id)
    if not exam:
        raise HTTPException(404, "Imtihon topilmadi")
    expected = {
        SkillType.READING: exam.reading_id,
        SkillType.LISTENING: exam.listening_id,
        SkillType.WRITING: exam.writing_id,
        SkillType.SPEAKING: exam.speaking_id,
    }[skill]
    if expected != submitted_test_id:
        raise HTTPException(403, "Bu skill uchun boshqa testdan foydalanish mumkin emas")
    attempt = await mock_services.get_or_create_attempt_for_user(
        db, exams_session.user.id, exam.id,
        registration_id=registration.id, session_id=registration.session_id,
    )
    skill_row = (await db.execute(
        select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == attempt.id,
            MockSkillAttempt.skill == skill,
        )
    )).scalar_one_or_none()
    if not skill_row:
        raise HTTPException(409, "Skill attempt topilmadi")
    if skill_row.submitted_at is not None:
        if not allow_submitted:
            raise HTTPException(409, "Bu skill allaqachon topshirilgan")
        return attempt, skill_row
    await mock_services.ensure_mock_attempt_active(db, attempt.id, exams_session.user.id)
    return attempt, skill_row


# =====================================================
# SKILL ANSWER SUBMISSION (Exams-auth bilan himoyalangan)
# =====================================================
# Reading/Listening/Writing/Speaking test KONTENTI (savollar) PUBLIC
# endpointlar orqali olinadi (masalan GET /reading-tests/{id}) — ular
# autentifikatsiya talab qilmaydi. Lekin JAVOB YUBORISH shu skill
# routerlarida `get_current_user`ga (asosiy Steply login) bog'langan —
# MK ID orqali kirgan Exams talabasining bunday sessiyasi umuman yo'q.
# Shu sababli javob yuborish uchun mustaqil "eshik" (exams-auth) orqali
# xuddi shu servis funksiyalariga murojaat qiluvchi ushbu proksi
# endpointlar qo'shildi — baholash logikasi ikki marta yozilmaydi.


def _get_reading_service(db: AsyncSession = Depends(get_db)) -> ReadingService:
    return ReadingService(db)


def _get_listening_service(db: AsyncSession = Depends(get_db)) -> ListeningService:
    return ListeningService(db)


def _get_writing_service(db: AsyncSession = Depends(get_db)) -> WritingService:
    return WritingService(db)


def _get_speaking_service(db: AsyncSession = Depends(get_db)) -> SpeakingService:
    return SpeakingService(db)


async def _require_assigned_content(
    service: ExamsAuthService,
    db: AsyncSession,
    exams_session: ExamsSession,
    skill: SkillType,
    requested_test_id: str,
) -> None:
    """Kontentni (savollarni) faqat shu examga biriktirilgan testlar uchun beradi.

    Public GET /reading-tests/{id} kabi endpointlar umumiy; Exams talabasi esa
    faqat o'z examining 4 testini ko'ra olishi kerak (boshqa test -> 403).
    """
    registration = await service._load_registration(exams_session.registration_id)
    if registration.user_id != exams_session.user.id:
        raise HTTPException(403, "Exams seansi yaroqsiz")
    if registration.status not in {RegistrationStatus.CHECKED_IN, RegistrationStatus.IN_PROGRESS}:
        raise HTTPException(403, "Imtihon uchun check-in tasdiqlanmagan")
    exam = await db.get(MockExam, registration.mock_exam_id)
    if not exam or not exam.is_active:
        raise HTTPException(404, "Imtihon topilmadi yoki faol emas")
    expected = {
        SkillType.READING: exam.reading_id,
        SkillType.LISTENING: exam.listening_id,
        SkillType.WRITING: exam.writing_id,
        SkillType.SPEAKING: exam.speaking_id,
    }[skill]
    if not expected or expected != requested_test_id:
        raise HTTPException(403, "Bu test sizning imtihoningizga biriktirilmagan")


@exams_router.get("/reading/{test_id}", response_model=ReadingTestResponse, summary="Faqat biriktirilgan Reading test kontenti")
async def get_my_reading_content(
    test_id: str,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: ReadingService = Depends(_get_reading_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    await _require_assigned_content(service_auth, db, exams_session, SkillType.READING, test_id)
    test = await service.get_test_by_id(test_id)
    if not test:
        raise HTTPException(404, "Reading test topilmadi")
    return test


@exams_router.get("/listening/{exam_id}", response_model=ListeningExamResponse, summary="Faqat biriktirilgan Listening test kontenti")
async def get_my_listening_content(
    exam_id: str,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: ListeningService = Depends(_get_listening_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    await _require_assigned_content(service_auth, db, exams_session, SkillType.LISTENING, exam_id)
    exam = await service.get_exam_by_id(exam_id)
    if not exam:
        raise HTTPException(404, "Listening test topilmadi")
    return exam


@exams_router.get("/writing/{exam_id}", response_model=WritingExamRead, summary="Faqat biriktirilgan Writing test kontenti")
async def get_my_writing_content(
    exam_id: str,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: WritingService = Depends(_get_writing_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    await _require_assigned_content(service_auth, db, exams_session, SkillType.WRITING, exam_id)
    exam = await service.get_exam(exam_id)
    if not exam:
        raise HTTPException(404, "Writing test topilmadi")
    return exam


@exams_router.post("/reading/{test_id}/submit", response_model=ReadingResultDetailResponse)
async def submit_my_reading_answers(
    test_id: str,
    data: ReadingSubmitRequest,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: ReadingService = Depends(_get_reading_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    attempt, _ = await _require_assigned_skill(service_auth, db, exams_session, SkillType.READING, test_id, allow_submitted=True)
    data.exam_attempt_id = attempt.id
    result = await service.submit_answers(user_id=exams_session.user.id, test_id=test_id, data=data)
    await service_auth.end_session_if_all_submitted(attempt.id)
    return result


@exams_router.post("/listening/{exam_id}/submit", response_model=ListeningResultDetailResponse)
async def submit_my_listening_answers(
    exam_id: str,
    submission: ListeningSubmission,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: ListeningService = Depends(_get_listening_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    submission.exam_id = exam_id
    attempt, _ = await _require_assigned_skill(service_auth, db, exams_session, SkillType.LISTENING, exam_id, allow_submitted=True)
    submission.exam_attempt_id = attempt.id
    result = await service.submit_exam_and_get_result(user_id=exams_session.user.id, data=submission)
    await service_auth.end_session_if_all_submitted(attempt.id)
    return result


@exams_router.post("/writing/{exam_id}/submit", response_model=WritingResultRead)
async def submit_my_writing_answers(
    exam_id: str,
    payload: WritingSubmitRequest,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: WritingService = Depends(_get_writing_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    attempt, skill_row = await _require_assigned_skill(service_auth, db, exams_session, SkillType.WRITING, exam_id)
    payload.exam_attempt_id = attempt.id
    result = await service.submit_practice_answers(exams_session.user.id, exam_id, payload)
    # Writing qo'lda/AI baholanadi, lekin topshirilgan vaqti shu yerda belgilanadi
    skill_row = await db.get(type(skill_row), skill_row.id)
    if skill_row is not None and skill_row.submitted_at is None:
        skill_row.submitted_at = utcnow()
        await db.commit()
    await service_auth.end_session_if_all_submitted(attempt.id)
    return result


@exams_router.get("/speaking/me/assigned-test", response_model=SpeakingAssignedTestResponse)
async def my_assigned_speaking_test(
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(_get_speaking_service),
):
    from sqlalchemy import select

    from app.modules.exams.mock.models import (
        MockExamAttempt,
        MockSkillAttempt,
    )

    stmt = (
        select(MockSkillAttempt)
        .join(MockExamAttempt, MockSkillAttempt.attempt_id == MockExamAttempt.id)
        .where(
            MockSkillAttempt.user_id == exams_session.user.id,
            MockSkillAttempt.skill == SkillType.SPEAKING,
            MockSkillAttempt.speaking_test_id.is_not(None),
            MockExamAttempt.is_finished.is_(False),
            MockExamAttempt.registration_id == exams_session.registration_id,
        )
        .order_by(MockExamAttempt.started_at.desc())
        .limit(1)
    )
    skill_attempt = (await db.execute(stmt)).scalar_one_or_none()
    if not skill_attempt:
        raise HTTPException(404, "Siz uchun faol Speaking sessiya topilmadi")
    test = await service.get_assigned_test_for_skill_attempt(skill_attempt)
    return SpeakingAssignedTestResponse(skill_attempt_id=skill_attempt.id, speaking_test=test)


@exams_router.get("/speaking/submissions/{submission_id}/audio", summary="Exam-session Speaking audio")
async def exam_download_speaking_audio(
    submission_id: int,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.tests.speaking.models import SpeakingSubmission
    from app.modules.exams.mock.models import MockExamAttempt, MockSkillAttempt, SkillType

    stmt = (
        select(SpeakingSubmission)
        .join(MockSkillAttempt, MockSkillAttempt.id == SpeakingSubmission.mock_skill_attempt_id)
        .join(MockExamAttempt, MockExamAttempt.id == MockSkillAttempt.attempt_id)
        .where(
            SpeakingSubmission.id == submission_id,
            SpeakingSubmission.user_id == exams_session.user.id,
            MockSkillAttempt.skill == SkillType.SPEAKING,
            MockExamAttempt.registration_id == exams_session.registration_id,
            MockExamAttempt.user_id == exams_session.user.id,
        )
    )
    submission = (await db.execute(stmt)).scalar_one_or_none()
    if not submission:
        raise HTTPException(404, "Audio topilmadi")
    if not submission.audio_storage_key:
        raise HTTPException(404, "Audio topilmadi")
    path = ensure_private_file(_PRIVATE_ROOT / "speaking" / "submissions" / submission.audio_storage_key, "speaking")
    media = {".webm": "audio/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp4": "audio/mp4"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f'inline; filename="{path.name}"'})


@exams_router.post("/speaking/{speaking_test_id}/submit", response_model=SpeakingSubmissionResponse)
async def submit_my_speaking_audio(
    speaking_test_id: str,
    file: UploadFile = File(...),
    mock_skill_attempt_id: Optional[int] = None,
    exams_session: ExamsSession = Depends(get_current_exams_session),
    service: SpeakingService = Depends(_get_speaking_service),
    service_auth: ExamsAuthService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
):
    attempt, skill_row = await _require_assigned_skill(service_auth, db, exams_session, SkillType.SPEAKING, speaking_test_id)
    result = await service.upload_submission(
        exams_session.user.id, speaking_test_id, file, skill_row.id
    )
    result.audio_url = f"/exams/speaking/submissions/{result.id}/audio"
    skill_row = await db.get(type(skill_row), skill_row.id)
    if skill_row is not None and skill_row.submitted_at is None:
        skill_row.submitted_at = utcnow()
        await db.commit()
    await service_auth.end_session_if_all_submitted(attempt.id)
    return result