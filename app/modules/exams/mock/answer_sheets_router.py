import asyncio
import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import UserRole
from app.modules.exams.mock.models import ExamSession, MockExam, MockRegistration

# Answer sheet (applicantlar ismi/ID'si) — faqat platforma adminlari.
router = APIRouter(
    prefix='/admin/mock-sessions',
    tags=['Mock Sessions — Answer Sheets'],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)


@router.get('/{session_id}/applicants/{applicant_id}/answer-sheet.pdf')
async def download_applicant_answer_sheet(
    session_id: int, applicant_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Bitta applicant (MockRegistration) uchun answer sheet PDF yuklab olish."""
    from fastapi.responses import Response
    from app.modules.exams.mock.pdf_service import AnswerSheetPDFService

    session = await db.get(ExamSession, session_id)
    if not session:
        raise HTTPException(404, 'Sessiya topilmadi')
    exam = await db.get(MockExam, session.mock_exam_id)
    if not exam:
        raise HTTPException(404, 'Sessiya topilmadi')

    registration = await db.get(MockRegistration, applicant_id)
    if not registration or registration.session_id != session.id:
        raise HTTPException(404, 'Applicant topilmadi')

    exam_date = session.exam_date.strftime('%d.%m.%Y %H:%M') if session.exam_date else ''

    try:
        service = AnswerSheetPDFService()
        pdf_bytes = await asyncio.to_thread(service.generate_answer_sheet,
            user_full_name=registration.full_name,
            cefr_code=registration.student_id,
            exam_date=exam_date,
            exam_title=exam.title,
            student_id=registration.student_id,
        )
    except FileNotFoundError:
        raise HTTPException(503, 'Answer sheet template topilmadi — admin bilan bog\'laning')
    except Exception as e:
        raise HTTPException(500, 'PDF yaratishda server xatosi yuz berdi') from e

    import re
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', registration.full_name.replace(' ', '_'))[:40] or 'Unknown'
    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={
            'Content-Disposition': f'inline; filename="{registration.student_id}_{safe_name}.pdf"'
        },
    )


@router.get('/{session_id}/answer-sheets-bulk.zip')
async def download_bulk_applicant_answer_sheets(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Sessiyaning barcha applicantlari uchun answer sheets ZIP arxivda."""
    import io, zipfile
    from fastapi.responses import Response
    from app.modules.exams.mock.pdf_service import AnswerSheetPDFService

    session = await db.get(ExamSession, session_id)
    if not session:
        raise HTTPException(404, 'Sessiya topilmadi')
    exam = await db.get(MockExam, session.mock_exam_id)
    if not exam:
        raise HTTPException(404, 'Sessiya topilmadi')

    stmt = select(MockRegistration).where(MockRegistration.session_id == session_id).order_by(MockRegistration.created_at)
    registrations = (await db.execute(stmt)).scalars().all()

    if not registrations:
        raise HTTPException(404, 'Bu sessiyada applicantlar yo\'q')

    exam_date = session.exam_date.strftime('%d.%m.%Y %H:%M') if session.exam_date else ''

    service = AnswerSheetPDFService()
    zip_buf = io.BytesIO()
    errors: list[str] = []

    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, ap in enumerate(registrations, 1):
            try:
                pdf_bytes = await asyncio.to_thread(service.generate_answer_sheet,
                    user_full_name=ap.full_name,
                    cefr_code=ap.student_id,
                    exam_date=exam_date,
                    exam_title=exam.title,
                    student_id=ap.student_id,
                )
                safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', ap.full_name.replace(' ', '_'))[:40] or 'Unknown'
                zf.writestr(f'answer_sheets/{i:03d}_{ap.student_id}_{safe_name}.pdf', pdf_bytes)
            except Exception as e:
                errors.append(f'{ap.student_id}: {e}')

        if errors:
            zf.writestr('XATOLAR.txt', '\n'.join(errors))

    zip_buf.seek(0)
    return Response(
        content=zip_buf.read(),
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="answer-sheets-session-{session_id}.zip"'
        },
    )
