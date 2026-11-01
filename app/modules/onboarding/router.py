"""Onboarding endpointlari va admin imtihon sanalari."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User, UserRole

from . import service
from .models import ExamDate
from .schemas import (
    ExamDateIn, ExamDateOut, ExamDatePatch, OnboardingResponse, OnboardingUpdate,
)

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])
admin_router = APIRouter(prefix="/admin/exam-dates", tags=["Admin Onboarding"])

ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


def _to_response(profile) -> OnboardingResponse:
    return OnboardingResponse(
        cefr_taken=profile.cefr_taken,
        cefr_level=profile.cefr_level,
        estimated_level=profile.estimated_level,
        target_level=profile.target_level,
        vocab_band=profile.vocab_band,
        strengths=profile.strengths,
        weaknesses=profile.weaknesses,
        exam_date_id=profile.exam_date_id,
        daily_hours=profile.daily_hours,
        teacher_hours=profile.teacher_hours,
        is_complete=service.is_complete(profile),
        plan_version=profile.plan_version or 0,
        plan=profile.plan,
    )


@router.get("")
async def get_onboarding(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await service.get_or_create_profile(db, user.id)
    await db.commit()
    return _to_response(profile)


@router.put("")
async def update_onboarding(
    payload: OnboardingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Javoblarni qisman saqlaydi. Har bir o'zgarishda to'liqlik qayta hisoblanadi."""
    data = payload.model_dump(exclude_unset=True)
    if data.get("exam_date_id") is not None:
        await service.get_active_exam_date(db, data["exam_date_id"])
    profile = await service.get_or_create_profile(db, user.id)
    service.apply_update(profile, data)
    await db.commit()
    return _to_response(profile)


@router.get("/exam-dates", response_model=list[ExamDateOut])
async def list_exam_dates(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(
        select(ExamDate).where(ExamDate.is_active.is_(True)).order_by(ExamDate.exam_date.asc())
    )).all()
    return rows


@router.post("/plan")
async def create_plan(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    profile = await service.get_or_create_profile(db, user.id)
    plan = await service.generate_plan(db, profile)
    await db.commit()
    return {"plan_version": profile.plan_version, "plan": plan}


# ---------- Admin: imtihon sanalari ----------

@admin_router.get("", response_model=list[ExamDateOut])
async def admin_list_exam_dates(_: User = Depends(require_role(*ADMIN_ROLES)), db: AsyncSession = Depends(get_db)):
    return (await db.scalars(select(ExamDate).order_by(ExamDate.exam_date.asc()))).all()


@admin_router.post("", response_model=ExamDateOut, status_code=201)
async def admin_create_exam_date(
    payload: ExamDateIn,
    _: User = Depends(require_role(*ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    row = ExamDate(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@admin_router.patch("/{exam_id}", response_model=ExamDateOut)
async def admin_update_exam_date(
    exam_id: int,
    payload: ExamDatePatch,
    _: User = Depends(require_role(*ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ExamDate, exam_id)
    if row is None:
        raise HTTPException(404, "Imtihon sanasi topilmadi")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


@admin_router.delete("/{exam_id}", status_code=204)
async def admin_delete_exam_date(
    exam_id: int,
    _: User = Depends(require_role(*ADMIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ExamDate, exam_id)
    if row is None:
        raise HTTPException(404, "Imtihon sanasi topilmadi")
    row.is_active = False  # o'chirish o'rniga arxivlanadi (mavjud rejalar buzilmasligi uchun)
    await db.commit()
