"""Complete Mock Exam administration API."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User, UserRole
from . import registration_service, services
from .models import ExamSession, MockExam, MockRegistration, MockExamAttempt, SkillType
from .schemas import ExamSessionCreate, ExamSessionResponse, ExamSessionUpdate, MockExamUpdate, MockRegistrationResponse, MockExamAdminDetail, MockExamCreateWithAutoSelect
from app.modules.exams.mock.schemas import MockExamAttemptDetail, MockExamAdminDetail

router = APIRouter(prefix="/admin/mock-exams", tags=["Mock Exams — Admin"], dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))])


class ManualSkillGradeIn(BaseModel):
    skill: SkillType
    scaled_score: float = Field(..., ge=0, le=75)
    raw_score: Optional[float] = Field(None, ge=0)
    comment: Optional[str] = Field(None, max_length=2000)

# Static routes FIRST; /{exam_id} must stay after them.
@router.post("/sessions", response_model=ExamSessionResponse, status_code=201)
async def create_exam_session(
    data: ExamSessionCreate,
    db: AsyncSession = Depends(get_db),
):
    session = await registration_service.create_session(db, data)
    return {**session.__dict__, "registered_count": 0}

@router.get("/sessions", response_model=List[ExamSessionResponse])
async def list_all_sessions_admin(mock_exam_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    stmt = select(ExamSession).join(MockExam, MockExam.id == ExamSession.mock_exam_id).order_by(ExamSession.exam_date.desc())
    if mock_exam_id: stmt = stmt.where(ExamSession.mock_exam_id == mock_exam_id)
    rows = (await db.execute(stmt)).scalars().all()
    out=[]
    for s in rows:
        count = await db.scalar(select(func.count()).select_from(MockRegistration).where(MockRegistration.session_id == s.id)) or 0
        out.append({**s.__dict__, "registered_count": count})
    return out

@router.patch("/sessions/{session_id}", response_model=ExamSessionResponse)
async def update_exam_session(
    session_id: int,
    data: ExamSessionUpdate,
    db: AsyncSession = Depends(get_db),
):
    session = await registration_service.update_session(db, session_id, data)
    count = await db.scalar(select(func.count()).select_from(MockRegistration).where(MockRegistration.session_id == session_id)) or 0
    return {**session.__dict__, "registered_count": count}

@router.get("/sessions/{session_id}/registrations", response_model=List[MockRegistrationResponse])
async def list_session_registrations(session_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(ExamSession.id).join(MockExam, MockExam.id == ExamSession.mock_exam_id).where(ExamSession.id == session_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None: raise HTTPException(404, "Sessiya topilmadi")
    return await registration_service.list_session_registrations(db, session_id)

@router.post("/registrations/{registration_id}/confirm-attendance", response_model=MockRegistrationResponse)
async def confirm_attendance(registration_id: int, db: AsyncSession = Depends(get_db)):
    return await registration_service.confirm_attendance(db, registration_id)

@router.post("/attempts/{attempt_id}/manual-grade")
async def manual_grade_skill(
    attempt_id: int,
    data: ManualSkillGradeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await services.grade_skill_as_staff(db, current_user, attempt_id, data.skill, data.scaled_score, data.raw_score)

@router.get("/attempts/{attempt_id}", response_model=MockExamAttemptDetail)
async def admin_attempt_detail(attempt_id: int, db: AsyncSession = Depends(get_db)):
    stmt=select(MockExamAttempt).join(MockExam, MockExam.id == MockExamAttempt.mock_exam_id).options(selectinload(MockExamAttempt.skills),selectinload(MockExamAttempt.exam),selectinload(MockExamAttempt.registration)).where(MockExamAttempt.id==attempt_id)
    row=(await db.execute(stmt)).scalar_one_or_none()
    if not row: raise HTTPException(404,"Attempt topilmadi")
    return row



@router.delete("/sessions/{session_id}", status_code=204)
async def delete_exam_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Sessiyani o'chirish. Faqat registratsiya bo'lmagan bo'sh sessiyalar o'chiriladi."""
    from sqlalchemy import func as _func
    session = await db.get(ExamSession, session_id)
    if not session:
        raise HTTPException(404, "Sessiya topilmadi")
    reg_count = await db.scalar(select(_func.count()).select_from(MockRegistration).where(MockRegistration.session_id == session_id)) or 0
    if reg_count > 0:
        raise HTTPException(409, f"Bu sessiyada {reg_count} ta ro'yxat mavjud. Avval ularni o'chiring yoki bekor qiling.")
    await db.delete(session)
    await db.commit()

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mock_exam(data: MockExamCreateWithAutoSelect, db: AsyncSession = Depends(get_db)):
    return await services.create_exam(db, data)

@router.get("", response_model=List[MockExamAdminDetail])
async def list_mock_exams_admin(
    is_active: Optional[bool] = None,
    cefr_level: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Barcha mock examlar ro'yxati. is_active va cefr_level bo'yicha filter qilish mumkin."""
    stmt = select(MockExam).order_by(MockExam.created_at.desc())
    if is_active is not None:
        stmt = stmt.where(MockExam.is_active == is_active)
    if cefr_level:
        stmt = stmt.where(MockExam.cefr_level == cefr_level)
    return (await db.execute(stmt)).scalars().all()

@router.get("/{exam_id}", response_model=MockExamAdminDetail)
async def get_mock_exam_admin(exam_id: str, db: AsyncSession = Depends(get_db)):
    stmt=select(MockExam).options(selectinload(MockExam.reading_test),selectinload(MockExam.listening_test),selectinload(MockExam.writing_test)).where(MockExam.id==exam_id)
    exam=(await db.execute(stmt)).scalar_one_or_none()
    if not exam: raise HTTPException(404,"Imtihon topilmadi")
    return exam

@router.patch("/{exam_id}")
async def update_mock_exam(exam_id: str, data: MockExamUpdate, db: AsyncSession = Depends(get_db)):
    return await services.update_exam(db, exam_id, data)

@router.delete("/{exam_id}", status_code=204)
async def delete_mock_exam(exam_id: str, db: AsyncSession = Depends(get_db)):
    await services.delete_exam_service(db, exam_id)
