"""
paper_router.py
===============
Admin uchun chop etish variantlarini (paper-based) yuklab olish endpointlari.

Endpointlar:
  GET /mock-exams/admin/papers/{mock_exam_id}/listening.pdf
      → Listening question paper PDFi

  GET /mock-exams/admin/papers/{mock_exam_id}/reading.pdf
      → Reading question paper PDFi

  GET /mock-exams/admin/papers/{mock_exam_id}/writing.pdf
      → Writing question paper PDFi

  GET /mock-exams/admin/papers/{session_id}/bundle.zip
      → Bir sessiya uchun to'liq paket:
          listening.pdf + reading.pdf + writing.pdf
          + answer_sheets/ (har bir ro'yxatdan o'tgan talaba uchun)

  GET /mock-exams/admin/papers/{session_id}/answer-sheets-bulk.zip
      → Faqat javoblar varaqalari (barcha talabalar uchun, bitta ZIP)
"""

import asyncio
import io
import re
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import UserRole

from .models import ExamSession, MockExam, MockRegistration
from .paper_pdf_service import ListeningPaperPDF, ReadingPaperPDF, WritingPaperPDF
from .pdf_service import AnswerSheetPDFService

router = APIRouter(
    prefix="/admin/mock-exams/papers",
    tags=["Paper-Based Exam Downloads"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)


# ─────────────────────────────────────────────
#  YORDAMCHI
# ─────────────────────────────────────────────

async def _get_mock_exam(mock_exam_id: str, db: AsyncSession) -> MockExam:
    stmt = select(MockExam).where(MockExam.id == mock_exam_id)
    exam = (await db.execute(stmt)).scalar_one_or_none()
    if not exam:
        raise HTTPException(404, f"Mock imtihon topilmadi: {mock_exam_id}")
    return exam


# ─────────────────────────────────────────────
#  1. ALOHIDA PAPERLAR
# ─────────────────────────────────────────────

@router.get("/{mock_exam_id}/listening.pdf")
async def download_listening_paper(
    mock_exam_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Mock imtihon uchun Listening question paper PDFi."""
    exam = await _get_mock_exam(mock_exam_id, db)
    if not exam.listening_id:
        raise HTTPException(400, "Bu mock imtihonda Listening test biriktirilmagan")

    try:
        pdf_bytes = await ListeningPaperPDF().generate(exam.listening_id, db)
    except ValueError:
        raise HTTPException(404, "Paper uchun kerakli test topilmadi")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="listening-{mock_exam_id}.pdf"'
        },
    )


@router.get("/{mock_exam_id}/reading.pdf")
async def download_reading_paper(
    mock_exam_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Mock imtihon uchun Reading question paper PDFi."""
    exam = await _get_mock_exam(mock_exam_id, db)
    if not exam.reading_id:
        raise HTTPException(400, "Bu mock imtihonda Reading test biriktirilmagan")

    try:
        pdf_bytes = await ReadingPaperPDF().generate(exam.reading_id, db)
    except ValueError:
        raise HTTPException(404, "Paper uchun kerakli test topilmadi")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="reading-{mock_exam_id}.pdf"'
        },
    )


@router.get("/{mock_exam_id}/writing.pdf")
async def download_writing_paper(
    mock_exam_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Mock imtihon uchun Writing question paper PDFi."""
    exam = await _get_mock_exam(mock_exam_id, db)
    if not exam.writing_id:
        raise HTTPException(400, "Bu mock imtihonda Writing test biriktirilmagan")

    try:
        pdf_bytes = await WritingPaperPDF().generate(exam.writing_id, db)
    except ValueError:
        raise HTTPException(404, "Paper uchun kerakli test topilmadi")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="writing-{mock_exam_id}.pdf"'
        },
    )


# ─────────────────────────────────────────────
#  2. SESSION UCHUN JAVOBLAR VARAQALARI
# ─────────────────────────────────────────────

@router.get("/sessions/{session_id}/answer-sheets-bulk.zip")
async def download_bulk_answer_sheets(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Bitta sessiyaga ro'yxatdan o'tgan barcha talabalar uchun
    javoblar varaqalarini ZIP arxivda yuklab olish.
    
    Har bir talaba uchun alohida PDF:
      answer_sheets/{student_id}_{full_name}.pdf
    """
    session_stmt = select(ExamSession).join(MockExam, MockExam.id == ExamSession.mock_exam_id).where(ExamSession.id == session_id)
    session = (await db.execute(session_stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Sessiya topilmadi")

    exam = await _get_mock_exam(session.mock_exam_id, db)
    exam_title = exam.title if exam else "Mock imtihon"
    exam_date = session.exam_date.strftime("%d.%m.%Y %H:%M")

    # Ro'yxatdan o'tganlar
    stmt = (
        select(MockRegistration)
        .where(MockRegistration.session_id == session_id)
        .order_by(MockRegistration.id)
    )
    registrations = list((await db.execute(stmt)).scalars().all())

    if not registrations:
        raise HTTPException(404, "Bu sessiyada ro'yxatdan o'tganlar yo'q")

    service = AnswerSheetPDFService()
    zip_buf = io.BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for reg in registrations:
            student_id = reg.student_id or "NOID"
            full_name = reg.full_name or "Unknown"

            # Foydalanuvchi cefr_code ni olish (student_id = snapshot)
            cefr_code = reg.student_id or ""

            pdf_bytes = await asyncio.to_thread(service.generate_answer_sheet,
                user_full_name=full_name,
                cefr_code=cefr_code,
                exam_date=exam_date,
                exam_title=exam_title,
                student_id=cefr_code,
            )

            # Fayl nomi: 001_MK-010101ABC_Ali_Valiyev.pdf
            import re
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", full_name.replace(" ", "_"))[:40] or "Unknown"
            filename = f"{reg.id:03d}_{student_id}_{safe_name}.pdf"
            zf.writestr(f"answer_sheets/{filename}", pdf_bytes)

    zip_buf.seek(0)
    return Response(
        content=zip_buf.read(),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="answer-sheets-session-{session_id}.zip"'
        },
    )


# ─────────────────────────────────────────────
#  3. TO'LIQ PAKET (Listening + Reading + Writing + Answer Sheets)
# ─────────────────────────────────────────────

@router.get("/sessions/{session_id}/bundle.zip")
async def download_session_bundle(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Bitta sessiya uchun to'liq chop etish paketi (ZIP):
    
    bundle-{session_id}.zip/
      ├── listening-paper.pdf
      ├── reading-paper.pdf
      ├── writing-paper.pdf
      ├── davomat-ro'yxati.pdf   (roster — imzo uchun)
      └── answer_sheets/
              001_MK-xxx_Ali_Valiyev.pdf
              002_MK-yyy_Zulfiya_Qosimova.pdf
              ...
    """
    session_stmt = select(ExamSession).join(MockExam, MockExam.id == ExamSession.mock_exam_id).where(ExamSession.id == session_id)
    session = (await db.execute(session_stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Sessiya topilmadi")

    exam = await _get_mock_exam(session.mock_exam_id, db)
    if not exam:
        raise HTTPException(404, "Mock imtihon topilmadi")

    exam_title = exam.title
    exam_date = session.exam_date.strftime("%d.%m.%Y %H:%M")

    # Ro'yxatdan o'tganlar
    stmt = (
        select(MockRegistration)
        .where(MockRegistration.session_id == session_id)
        .order_by(MockRegistration.id)
    )
    registrations = list((await db.execute(stmt)).scalars().all())

    zip_buf = io.BytesIO()
    errors: list[str] = []

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # 1. Listening paper
        if exam.listening_id:
            try:
                l_pdf = await ListeningPaperPDF().generate(exam.listening_id, db)
                zf.writestr("listening-paper.pdf", l_pdf)
            except Exception as e:
                errors.append(f"Listening: {e}")

        # 2. Reading paper
        if exam.reading_id:
            try:
                r_pdf = await ReadingPaperPDF().generate(exam.reading_id, db)
                zf.writestr("reading-paper.pdf", r_pdf)
            except Exception as e:
                errors.append(f"Reading: {e}")

        # 3. Writing paper
        if exam.writing_id:
            try:
                w_pdf = await WritingPaperPDF().generate(exam.writing_id, db)
                zf.writestr("writing-paper.pdf", w_pdf)
            except Exception as e:
                errors.append(f"Writing: {e}")

        # 4. Davomat ro'yxati (roster)
        try:
            from .pdf_service import ReferralPDFService
            roster_pdf = await ReferralPDFService().generate_roster(
                session, exam_title, registrations
            )
            zf.writestr("davomat-royxati.pdf", roster_pdf)
        except Exception as e:
            errors.append(f"Roster: {e}")

        # 5. Javoblar varaqalari (har bir talaba)
        as_service = AnswerSheetPDFService()
        for reg in registrations:
            try:
                student_id = reg.student_id or ""
                full_name = reg.full_name or "Unknown"
                pdf_bytes = await asyncio.to_thread(as_service.generate_answer_sheet,
                    user_full_name=full_name,
                    cefr_code=student_id,
                    exam_date=exam_date,
                    exam_title=exam_title,
                    student_id=student_id,
                )
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", full_name.replace(" ", "_"))[:40] or "Unknown"
                filename = f"{reg.id:03d}_{student_id}_{safe_name}.pdf"
                zf.writestr(f"answer_sheets/{filename}", pdf_bytes)
            except Exception as e:
                errors.append(f"AnswerSheet {reg.id}: {e}")

        # Xatolar varaqasi
        if errors:
            error_text = "\n".join(errors)
            zf.writestr("XATOLAR.txt", error_text)

    zip_buf.seek(0)
    return Response(
        content=zip_buf.read(),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="bundle-session-{session_id}.zip"'
        },
    )
