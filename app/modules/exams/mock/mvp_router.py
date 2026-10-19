"""Canonical Mock Center MVP workflow.

This router intentionally models the paper-based exam lifecycle described in
mock_center.uz specification: registration -> check-in -> paper grading ->
speaking -> final result.
"""
from datetime import datetime, timezone
import io
import qrcode

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import UserRole
from app.modules.auth.models import User, UserContact, UserProfile, ContactType
from .models import (
    ExamSession,
    MockExam,
    MockExamAttempt,
    MockExamResult,
    MockRegistration,
    MockSkillAttempt,
    RegistrationStatus,
    SkillType,
)
from . import registration_service
from . import services as services_mod
from .services import finish_exam_service

router = APIRouter(prefix="/mock-exams/center", tags=["Mock Center MVP"])
admin = APIRouter(
    prefix="/admin/mock-exams/center",
    tags=["Mock Center MVP — Admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)


class CheckInRequest(BaseModel):
    student_id: str = Field(..., min_length=3, max_length=32)
    session_id: int | None = None


class ManualScoreRequest(BaseModel):
    scaled_score: float = Field(..., ge=0, le=75)
    raw_score: float | None = Field(None, ge=0)


class FinalizeRequest(BaseModel):
    send_sms: bool = False


async def _find_registration(db: AsyncSession, student_id: str, session_id: int | None):
    stmt = (
        select(MockRegistration)
        .join(MockExam, MockExam.id == MockRegistration.mock_exam_id)
        .where(MockRegistration.student_id == student_id)
        .order_by(MockRegistration.created_at.desc())
    )
    if session_id is not None:
        stmt = stmt.where(MockRegistration.session_id == session_id)
    registration = (await db.execute(stmt)).scalars().first()
    return registration


@router.get("/me/registrations")
async def my_registrations(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(MockRegistration)
        .options(selectinload(MockRegistration.session))
        .where(MockRegistration.user_id == current_user.id)
        .order_by(MockRegistration.created_at.desc())
    )).scalars().all()
    return [
        {
            "id": r.id,
            "student_id": r.student_id,
            "full_name": r.full_name,
            "status": r.status,
            "session_id": r.session_id,
            "exam_date": r.session.exam_date if r.session else None,
            "center_name": r.session.center_name if r.session else None,
            "room_name": r.session.room_name if r.session else None,
            "location_address": r.session.location_address if r.session else None,
            "region": r.session.region if r.session else None,
        }
        for r in rows
    ]


@router.get("/me/registrations/{registration_id}/permit.pdf")
async def registration_permit(
    registration_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(MockRegistration, registration_id)
    if not r or r.user_id != current_user.id:
        raise HTTPException(404, "Qaydnoma topilmadi")
    session = await db.get(ExamSession, r.session_id)
    exam = await db.get(MockExam, r.mock_exam_id)
    if not session or not exam:
        raise HTTPException(404, "Imtihon sessiyasi topilmadi")

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Qaydnomа - {r.student_id}")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(60, height - 70, "MOCK CENTER — QAYDNOMA")
    pdf.setFont("Helvetica", 11)
    rows = [
        ("F.I.Sh.", r.full_name),
        ("Student ID", r.student_id),
        ("Imtihon", exam.title),
        ("Sana", session.exam_date.strftime("%d.%m.%Y %H:%M")),
        ("Markaz", session.center_name or "-"),
        ("Zal", session.room_name or "-"),
        ("Manzil", session.location_address),
    ]
    y = height - 120
    for label, value in rows:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(60, y, f"{label}:")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(150, y, str(value))
        y -= 24

    qr = qrcode.make(r.qr_token)
    qr_buf = io.BytesIO()
    qr.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    from reportlab.lib.utils import ImageReader
    pdf.drawImage(ImageReader(qr_buf), width - 190, height - 290, width=120, height=120)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(width - 190, height - 305, "QR identifikator")
    pdf.showPage()
    pdf.save()
    buf.seek(0)
    return Response(buf.read(), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="permit-{r.student_id}.pdf"'
    })


@router.get("/me/registrations/{registration_id}")
async def my_registration(
    registration_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(MockRegistration, registration_id)
    if not r or r.user_id != current_user.id:
        raise HTTPException(404, "Qaydnoma topilmadi")
    session = await db.get(ExamSession, r.session_id)
    exam = await db.get(MockExam, r.mock_exam_id)
    return {
        "registration": r,
        "exam": {"id": exam.id, "title": exam.title} if exam else None,
        "session": session,
        "identifier": r.student_id,
    }


@admin.post("/check-in")
async def check_in(data: CheckInRequest, db: AsyncSession = Depends(get_db)):
    """Exam-day lookup: Student ID -> registration -> allow/reject."""
    registration = await _find_registration(db, data.student_id.strip().upper(), data.session_id)
    if not registration:
        return {"allowed": False, "reason": "REGISTRATION_NOT_FOUND", "student_id": data.student_id}
    if registration.status == RegistrationStatus.CANCELLED:
        return {"allowed": False, "reason": "REGISTRATION_CANCELLED", "student_id": registration.student_id}
    if registration.status == RegistrationStatus.CHECKED_IN:
        attempt = (await db.execute(select(MockExamAttempt).where(MockExamAttempt.registration_id == registration.id))).scalar_one_or_none()
        return {"allowed": True, "already_checked_in": True, "registration": registration, "attempt_id": attempt.id if attempt else None}

    attempt = await registration_service.check_in_registration(db, registration, strict=True)

    return {
        "allowed": True,
        "already_checked_in": False,
        "registration": registration,
        "attempt_id": attempt.id,
        "message": "Abituriyent imtihonga kiritildi",
    }


@admin.get("/check-in/search")
async def check_in_search(student_id: str, session_id: int | None = None, db: AsyncSession = Depends(get_db)):
    registration = await _find_registration(db, student_id.strip().upper(), session_id)
    if not registration:
        return {"found": False}
    session = await db.get(ExamSession, registration.session_id)
    exam = await db.get(MockExam, registration.mock_exam_id)
    return {
        "found": True,
        "registration": registration,
        "session": session,
        "exam": {"id": exam.id, "title": exam.title} if exam else None,
    }


@admin.get("/writing/pending")
async def writing_pending(
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
):
    """Return Mock Center Writing attempts for manual grading, keyed by attempt ID."""
    stmt = (
        select(MockSkillAttempt, MockExamAttempt, MockExam, MockRegistration, UserProfile)
        .join(MockExamAttempt, MockSkillAttempt.attempt_id == MockExamAttempt.id)
        .join(MockExam, MockExamAttempt.mock_exam_id == MockExam.id)
        .outerjoin(MockRegistration, MockExamAttempt.registration_id == MockRegistration.id)
        .outerjoin(UserProfile, MockExamAttempt.user_id == UserProfile.user_id)
        .where(MockSkillAttempt.skill == SkillType.WRITING)
        .order_by(MockExamAttempt.started_at.desc())
    )
    if status == "pending":
        stmt = stmt.where(MockSkillAttempt.is_checked.is_(False))
    elif status == "graded":
        stmt = stmt.where(MockSkillAttempt.is_checked.is_(True))
    elif status != "all":
        raise HTTPException(400, "status pending, graded yoki all bo'lishi kerak")
    rows = (await db.execute(stmt)).all()
    return [
        {
            "skill_attempt_id": skill.id,
            "attempt_id": attempt.id,
            "user_id": attempt.user_id,
            "full_name": (profile.full_name if profile and profile.full_name else (reg.full_name if reg else "Abituriyent")),
            "student_id": (reg.student_id if reg else None),
            "mock_exam_title": exam.title,
            "exam_date": None,
            "is_checked": skill.is_checked,
            "scaled_score": skill.scaled_score or 0,
            "submitted_at": skill.submitted_at,
        }
        for skill, attempt, exam, reg, profile in rows
    ]

@admin.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = (
        select(MockExamAttempt)
        .join(MockExam, MockExam.id == MockExamAttempt.mock_exam_id)
        .options(selectinload(MockExamAttempt.skills))
        .where(MockExamAttempt.id == attempt_id)
    )
    attempt = (await db.execute(stmt)).scalar_one_or_none()
    if not attempt:
        raise HTTPException(404, "Imtihon urinish topilmadi")
    return attempt


@admin.post("/attempts/{attempt_id}/skills/{skill}/score")
async def score_skill(
    attempt_id: int,
    skill: SkillType,
    data: ManualScoreRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if skill not in (SkillType.WRITING, SkillType.SPEAKING):
        raise HTTPException(400, "Bu endpoint faqat Writing va Speaking uchun")
    return await services_mod.grade_skill_as_staff(
        db, current_user, attempt_id, skill, data.scaled_score, data.raw_score
    )


@admin.post("/attempts/{attempt_id}/finalize", response_model=dict)
async def finalize_attempt(
    attempt_id: int,
    data: FinalizeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Finalize only after all four skills have verified scores."""
    # finish_exam_service yagona finalizer (finalize.py) orqali FINALIZED holatiga o'tkazadi;
    # to'liq bo'lmasa 409 qaytaradi. Holatni bu yerda qo'lda o'zgartirmaymiz.
    result = await finish_exam_service(db, attempt_id, require_all_checked=True)

    sms_sent = False
    if data.send_sms:
        sms_sent = await _send_result_sms(db, result)
    return {"result": result, "sms_sent": sms_sent}


@router.get("/me/results")
async def my_results(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(MockExamResult)
        .where(MockExamResult.user_id == current_user.id, MockExamResult.status == "FINALIZED")
        .order_by(MockExamResult.created_at.desc())
    )).scalars().all()
    return rows


@router.get("/me/results/{result_id}")
async def my_result(result_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.get(MockExamResult, result_id)
    if not result or result.user_id != current_user.id or result.status != "FINALIZED":
        raise HTTPException(404, "Natija topilmadi")
    return result


@router.get("/me/results/{result_id}/pdf")
async def my_result_pdf(result_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.get(MockExamResult, result_id)
    if not result or result.user_id != current_user.id or result.status != "FINALIZED":
        raise HTTPException(404, "Natija topilmadi")
    profile = await db.get(UserProfile, current_user.id)
    # MK ID natijaga bog'langan urinishdagi ro'yxatdan olinadi (profilda yo'q).
    attempt = await db.get(MockExamAttempt, result.attempt_id)
    reg = (
        await db.get(MockRegistration, attempt.registration_id)
        if attempt and attempt.registration_id
        else None
    )
    student_id = reg.student_id if reg else f"USER-{current_user.id}"
    full_name = (
        (reg.full_name if reg and reg.full_name else None)
        or (profile.full_name if profile and profile.full_name else None)
        or "Abituriyent"
    )

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Mock Center Result - {student_id}")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(60, height - 70, "MOCK CENTER — FINAL RESULT")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(60, height - 105, f"Student: {full_name}")
    pdf.drawString(60, height - 123, f"ID: {student_id}")
    y = height - 175
    for label, value in [
        ("Listening", result.listening_ball),
        ("Reading", result.reading_ball),
        ("Writing", result.writing_ball),
        ("Speaking", result.speaking_ball),
        ("Overall", result.overall_score),
    ]:
        pdf.setFont("Helvetica-Bold" if label == "Overall" else "Helvetica", 12)
        pdf.drawString(70, y, f"{label:<12} {value:.1f} / 75")
        y -= 25
    pdf.drawString(70, y - 10, f"Status: {result.status}")
    pdf.drawString(70, y - 30, f"CEFR: {result.cefr_level}")
    pdf.save()
    buf.seek(0)
    return Response(buf.read(), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="result-{student_id}.pdf"'
    })


async def _send_result_sms(db: AsyncSession, result: MockExamResult) -> bool:
    """Optional SMS adapter. Never blocks result finalization when provider is not configured."""
    from app.core.config import settings
    if not settings.RESULT_SMS_ENABLED or not settings.ESKIZ_EMAIL or not settings.ESKIZ_PASSWORD:
        return False
    # IDEMPOTENTLIK: finish_exam_service allaqachon-yakunlangan attemptda
    # short-circuit qilib eski natijani qaytaradi, lekin bu funksiya
    # avvalgi holatda sms_sent_at'ni tekshirmasdi — shu sababli admin
    # "Finalize & SMS" tugmasini ikkinchi marta bossa (yoki so'rov qayta
    # yuborilsa), talabaga bir xil "natijangiz tayyor" SMS qayta-qayta
    # yuborilaverardi.
    if result.sms_sent_at is not None:
        return False
    contact = (await db.execute(
        select(UserContact.value).where(
            UserContact.user_id == result.user_id,
            UserContact.contact_type == ContactType.PHONE,
            UserContact.is_verified.is_(True),
        )
    )).scalar_one_or_none()
    if not contact:
        return False
    try:
        from app.modules.auth.sms import SmsService
        SmsService.LOGIN_EMAIL = settings.ESKIZ_EMAIL
        SmsService.LOGIN_PASSWORD = settings.ESKIZ_PASSWORD
        SmsService.send_result_ready(contact)
        result.sms_sent_at = datetime.now(timezone.utc)
        await db.commit()
        return True
    except Exception:
        return False


# Public/user routes and admin routes are included separately in router.py.
