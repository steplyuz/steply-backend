"""Administrator API: dashboard, users, registrations, attempts, results and OMR review."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
import uuid
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.auth.schemas import StatusResponse
from app.modules.admin.schemas import AdminDashboardResponse, StatisticsResponse, TrafficResponse, AdminUserUpdate, AdminUserSummary, AdminRegistrationStatusUpdate, SmsRegistrationSetting
from app.modules.admin.system_settings import SystemSettingsService, SMS_REGISTRATION_ENABLED
from app.modules.admin.service import AdminService
from app.modules.exams.mock.models import (
    ExamSession, MockExam, MockRegistration, MockExamAttempt, MockExamResult,
    RegistrationStatus,
)

router = APIRouter(prefix="/admin", tags=["Admin"])
_admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))
# Tizim sozlamalari: ADMIN va SUPER_ADMIN. Student/Teacher — 403.
_settings_admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))


async def _latest_mk_ids(db: AsyncSession, user_ids: list) -> dict:
    """user_id -> eng oxirgi MK ID (mock_registrations.student_id).

    MK ID endi profilda emas, imtihon ro'yxatida yashaydi va bitta odamda
    bir nechta bo'lishi mumkin — admin ro'yxatida eng so'nggisi ko'rsatiladi.
    """
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(MockRegistration.user_id, MockRegistration.student_id)
            .where(MockRegistration.user_id.in_(user_ids))
            .order_by(MockRegistration.created_at.asc())
        )
    ).all()
    return {user_id: student_id for user_id, student_id in rows}


def _get_service(db: AsyncSession = Depends(get_db)) -> AdminService:
    return AdminService(db)


@router.get("/settings/sms-registration", response_model=SmsRegistrationSetting)
async def get_sms_registration_setting(db: AsyncSession = Depends(get_db), _: User = _settings_admin_dep):
    return SmsRegistrationSetting(
        sms_registration_enabled=await SystemSettingsService.sms_registration_enabled(db)
    )


@router.put("/settings/sms-registration", response_model=SmsRegistrationSetting)
@router.patch("/settings/sms-registration", response_model=SmsRegistrationSetting)
async def set_sms_registration_setting(
    data: SmsRegistrationSetting,
    db: AsyncSession = Depends(get_db),
    admin: User = _settings_admin_dep,
):
    """SMS orqali RO'YXATDAN O'TISHni yoqadi/o'chiradi (DB'da saqlanadi, restartdan keyin ham qoladi).
    Login, Telegram, parolni tiklash va natija-SMS'ga ta'sir qilmaydi."""
    await SystemSettingsService.set_bool(
        db, SMS_REGISTRATION_ENABLED, data.sms_registration_enabled, updated_by=admin.id
    )
    await db.commit()
    return SmsRegistrationSetting(sms_registration_enabled=data.sms_registration_enabled)


@router.get("/statistics", response_model=StatisticsResponse)
async def get_statistics(_: User = _admin_dep, service: AdminService = Depends(_get_service)):
    return await service.get_statistics()


@router.get("/statistics/traffic", response_model=TrafficResponse)
async def get_traffic(
    period: str = Query("week", pattern="^(week|month|last7|last30)$"),
    _: User = _admin_dep,
    service: AdminService = Depends(_get_service),
):
    """Haftalik / oylik kunlik trafik (yangi foydalanuvchilar, practice urinishlar, mock natijalar, AI tekshiruvlar)."""
    return await service.get_traffic(period)


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def admin_dashboard(db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    from app.modules.exams.paper.models import OMRSubmission, OMRSubmissionStatus
    from app.modules.billing.models import BillingOrder, BillingOrderStatus
    return {
        "users": await db.scalar(select(func.count(User.id))) or 0,
        "active_users": await db.scalar(select(func.count(User.id)).where(User.is_active.is_(True))) or 0,
        "mock_exams": await db.scalar(select(func.count(MockExam.id))) or 0,
        "sessions": await db.scalar(select(func.count(ExamSession.id))) or 0,
        "registrations": await db.scalar(select(func.count(MockRegistration.id))) or 0,
        "attempts": await db.scalar(select(func.count(MockExamAttempt.id))) or 0,
        "final_results": await db.scalar(select(func.count(MockExamResult.id)).where(MockExamResult.status == "FINALIZED")) or 0,
        "omr_pending": await db.scalar(select(func.count(OMRSubmission.id)).where(OMRSubmission.status.in_([OMRSubmissionStatus.PENDING, OMRSubmissionStatus.PROCESSING, OMRSubmissionStatus.NEEDS_REVIEW, OMRSubmissionStatus.FAILED]))) or 0,
        "teachers": await db.scalar(select(func.count(User.id)).where(User.global_role == UserRole.TEACHER)) or 0,
        "paid_orders": await db.scalar(select(func.count(BillingOrder.id)).where(BillingOrder.status == BillingOrderStatus.PAID)) or 0,
        "pending_orders": await db.scalar(select(func.count(BillingOrder.id)).where(BillingOrder.status.in_([BillingOrderStatus.PENDING_PAYMENT, BillingOrderStatus.UNDER_REVIEW]))) or 0,
        "confirmed_revenue": await db.scalar(select(func.coalesce(func.sum(BillingOrder.amount), 0)).where(BillingOrder.status == BillingOrderStatus.PAID)) or 0,
    }


@router.get("/export/users")
async def export_users(_: User = _admin_dep, service: AdminService = Depends(_get_service)):
    file_path = await service.generate_users_excel()
    def _stream():
        try:
            with open(file_path, "rb") as fh:
                yield from fh
        finally:
            try: os.unlink(file_path)
            except OSError: pass
    return StreamingResponse(_stream(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=users_export.xlsx", "Cache-Control": "no-store"})


@router.get("/users", response_model=List[AdminUserSummary])
async def admin_users(search: Optional[str] = Query(None, max_length=100), role: Optional[UserRole] = None, is_active: Optional[bool] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    stmt = select(User).options(selectinload(User.profile)).order_by(User.created_at.desc()).offset(offset).limit(limit)
    if role is not None: stmt = stmt.where(User.global_role == role)
    if is_active is not None: stmt = stmt.where(User.is_active.is_(is_active))
    if search:
        q=f"%{search.strip()}%"
        # MK ID bo'yicha qidiruv endi mock_registrations orqali ketadi.
        stmt=stmt.outerjoin(UserProfile).outerjoin(MockRegistration, MockRegistration.user_id==User.id).where(or_(UserProfile.full_name.ilike(q), UserProfile.username.ilike(q), MockRegistration.student_id.ilike(q)))
    rows=(await db.execute(stmt)).scalars().unique().all()
    mk_ids = await _latest_mk_ids(db, [u.id for u in rows])
    return [AdminUserSummary(id=u.id,is_active=u.is_active,global_role=u.global_role,full_name=u.profile.full_name if u.profile else None,username=u.profile.username if u.profile else None,mk_id=mk_ids.get(u.id),created_at=u.created_at) for u in rows]


@router.get("/users/{user_id}")
async def admin_user_detail(user_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    stmt=select(User).options(selectinload(User.profile),selectinload(User.contacts),selectinload(User.sessions)).where(User.id==user_id)
    u=(await db.execute(stmt)).scalar_one_or_none()
    if not u: raise HTTPException(404,"Foydalanuvchi topilmadi")
    return {"id":u.id,"is_active":u.is_active,"global_role":u.global_role,"profile":u.profile,"contacts":u.contacts,"sessions":[{"id":str(s.id),"expires_at":s.expires_at,"is_revoked":s.is_revoked} for s in u.sessions]}


@router.patch("/users/{user_id}", response_model=AdminUserSummary)
async def admin_update_user(user_id: uuid.UUID, data: AdminUserUpdate, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    u=(await db.execute(select(User).options(selectinload(User.profile)).where(User.id==user_id))).scalar_one_or_none()
    if not u: raise HTTPException(404,"Foydalanuvchi topilmadi")
    update_data = data.model_dump(exclude_unset=True)
    # SUPER_ADMIN darajasi shu yerdan berilmasligi kerak: bu rol FastAdmin
    # panelga (barcha 65+ jadvalga cheklovsiz CRUD) kirish huquqi beradi, va
    # faqat promote_to_super_admin.py orqali, DB'ga to'g'ridan-to'g'ri kirish
    # huquqi bo'lgan kishi tomonidan qo'lda berilishi kerak — aks holda oddiy
    # ADMIN o'zini (yoki boshqa birovni) SUPER_ADMIN qilib, butun bazaga
    # cheklovsiz kirish huquqiga ega bo'lib qolardi.
    if update_data.get("global_role") == UserRole.SUPER_ADMIN:
        raise HTTPException(
            403,
            "SUPER_ADMIN roli shu endpoint orqali berilmaydi",
        )
    for k,v in update_data.items(): setattr(u,k,v)
    await db.commit(); await db.refresh(u)
    mk_ids = await _latest_mk_ids(db, [u.id])
    return AdminUserSummary(id=u.id,is_active=u.is_active,global_role=u.global_role,full_name=u.profile.full_name if u.profile else None,username=u.profile.username if u.profile else None,mk_id=mk_ids.get(u.id),created_at=u.created_at)


@router.get("/registrations")
async def admin_registrations(session_id: Optional[int] = None, status_filter: Optional[str] = Query(None, alias="status"), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    stmt=select(MockRegistration).options(selectinload(MockRegistration.user),selectinload(MockRegistration.session)).order_by(MockRegistration.created_at.desc()).offset(offset).limit(limit)
    if session_id: stmt=stmt.where(MockRegistration.session_id==session_id)
    if status_filter:
        # Boshqa admin endpointlari (registration status PATCH, /omr/submissions,
        # /results) statusni case-insensitive qabul qiladi -- shu yerda ham
        # xuddi shunday: "registered" ham, "REGISTERED" ham ishlashi kerak.
        try: stmt=stmt.where(MockRegistration.status==RegistrationStatus(status_filter.upper()))
        except ValueError as exc: raise HTTPException(422,f"Noto'g'ri status: {status_filter}") from exc
    return (await db.execute(stmt)).scalars().all()


@router.patch("/registrations/{registration_id}/status")
async def admin_registration_status(registration_id: int, data: AdminRegistrationStatusUpdate, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    reg=await db.get(MockRegistration,registration_id)
    if not reg: raise HTTPException(404,"Ro'yxatdan o'tish topilmadi")
    try: new_status=RegistrationStatus(data.status.upper())
    except ValueError as exc: raise HTTPException(422,f"Noto'g'ri status: {data.status}") from exc
    reg.status=new_status
    reg.attended_confirmed_at=datetime.now(timezone.utc) if new_status==RegistrationStatus.CHECKED_IN else None
    await db.commit(); await db.refresh(reg)
    return reg


@router.get("/attempts")
async def admin_attempts(session_id: Optional[int]=None,user_id:Optional[uuid.UUID]=None,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),db:AsyncSession=Depends(get_db),_:User=_admin_dep):
    stmt=select(MockExamAttempt).options(selectinload(MockExamAttempt.skills),selectinload(MockExamAttempt.exam),selectinload(MockExamAttempt.registration)).order_by(MockExamAttempt.started_at.desc()).offset(offset).limit(limit)
    if session_id: stmt=stmt.where(MockExamAttempt.session_id==session_id)
    if user_id: stmt=stmt.where(MockExamAttempt.user_id==user_id)
    return (await db.execute(stmt)).scalars().unique().all()


@router.get("/sessions")
async def admin_sessions(
    user_id: Optional[uuid.UUID] = None,
    is_revoked: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = _admin_dep,
):
    """Foydalanuvchi seanslarini ko'rish (admin uchun)."""
    from app.modules.auth.models import UserSession
    stmt = (
        select(UserSession)
        .order_by(UserSession.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if user_id:
        stmt = stmt.where(UserSession.user_id == user_id)
    if is_revoked is not None:
        stmt = stmt.where(UserSession.is_revoked.is_(is_revoked))
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.post("/users/{user_id}/sessions/revoke-all", response_model=StatusResponse)
async def admin_revoke_all_sessions(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = _admin_dep,
):
    """Foydalanuvchining barcha seanslarini bekor qilish."""
    from app.modules.auth.models import UserSession
    from sqlalchemy import update
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.is_revoked.is_(False))
        .values(is_revoked=True)
    )
    await db.commit()
    return {"status": "success", "message": "Barcha seanslar bekor qilindi"}


@router.get("/results")
async def admin_results(status_filter:Optional[str]=Query(None,alias="status"),offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),db:AsyncSession=Depends(get_db),_:User=_admin_dep):
    stmt=select(MockExamResult).order_by(MockExamResult.created_at.desc()).offset(offset).limit(limit)
    if status_filter: stmt=stmt.where(MockExamResult.status==status_filter.upper())
    return (await db.execute(stmt)).scalars().all()


@router.get("/omr/submissions")
async def admin_omr_submissions(module:Optional[str]=None,status_filter:Optional[str]=Query(None,alias="status"),candidate_id:Optional[str]=None,limit:int=Query(100,ge=1,le=500),db:AsyncSession=Depends(get_db),_:User=_admin_dep):
    from app.modules.exams.paper.models import OMRSubmission, PaperModule, OMRSubmissionStatus
    stmt=select(OMRSubmission).order_by(OMRSubmission.created_at.desc()).limit(limit)
    if module:
        try: stmt=stmt.where(OMRSubmission.module==PaperModule(module.upper()))
        except ValueError as exc: raise HTTPException(422,f"Noto'g'ri module: {module}") from exc
    if status_filter:
        try: stmt=stmt.where(OMRSubmission.status==OMRSubmissionStatus(status_filter.upper()))
        except ValueError as exc: raise HTTPException(422,f"Noto'g'ri OMR status: {status_filter}") from exc
    if candidate_id: stmt=stmt.where(OMRSubmission.candidate_id==candidate_id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/omr/submissions/{submission_id}")
async def admin_omr_submission(submission_id:int,db:AsyncSession=Depends(get_db),_:User=_admin_dep):
    from app.modules.exams.paper.models import OMRSubmission
    row=await db.get(OMRSubmission,submission_id)
    if not row: raise HTTPException(404,"OMR submission topilmadi")
    return row


@router.patch("/omr/submissions/{submission_id}/status")
async def admin_omr_status(submission_id:int,status_value:str=Query(...,alias="status"),db:AsyncSession=Depends(get_db),_:User=_admin_dep):
    from app.modules.exams.paper.models import OMRSubmission, OMRSubmissionStatus
    row=await db.get(OMRSubmission,submission_id)
    if not row: raise HTTPException(404,"OMR submission topilmadi")
    try: row.status=OMRSubmissionStatus(status_value.upper())
    except ValueError as exc: raise HTTPException(422,f"Noto'g'ri status: {status_value}") from exc
    await db.commit(); await db.refresh(row)
    return row
