"""
user_router.py
===============
Asosiy Steply saytidagi (steply.uz) foydalanuvchi uchun Mock imtihonlar
bilan ishlash endpointlari: ro'yxatni ko'rish, sotib olish so'rovi, ochiq
test sanalariga yozilish va o'z ro'yxatlarini ko'rish.

IMTIHON TOPSHIRISH OQIMI BU YERDA EMAS
---------------------------------------
Ilgari shu faylda `start`, `attempts/{id}/status`,
`attempts/{id}/submit/{skill}`, `attempts/{id}/finish`,
`attempts/{id}/result` endpointlari ham bor edi — ular
`exams_auth/router.py` dagi `/api/v1/exams/*` endpointlari bilan AYNAN bir
xil `mock_test.services` funksiyalarini chaqirardi, farqi faqat auth
"eshigi"da edi (Steply tokeni vs Exams tokeni).

Ikkita yo'l ochiq turishi xavfsizlik teshigi ham edi: exams-auth
(MK ID + SMS + ochiq seans) tekshiruvlarini aylanib o'tib, oddiy Steply
tokeni bilan imtihonni boshlash mumkin edi. Shuning uchun imtihon
topshirish oqimi YAGONA joyda — `/api/v1/exams/*` da qoldirildi.

Natijalar `/api/v1/exams/attempts/{id}/result` va
`/api/v1/mock-test/center/me/results` orqali olinadi.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User

from . import registration_service, services
from .schemas import (
    ExamSessionResponse,
    MockPurchaseResponse,
    MockRegistrationCreate,
    MockRegistrationResponse,
    UserMockExamResponse,
)

router = APIRouter(prefix="/mock-exams", tags=["Mock Exams (User)"])


# =====================================================
# RO'YXAT VA SOTIB OLISH
# =====================================================

@router.get("", response_model=List[UserMockExamResponse])
async def list_mock_exams(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await services.list_user_exams(db, current_user.id)


@router.post("/{exam_id}/buy", response_model=MockPurchaseResponse)
async def request_purchase(
    exam_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await services.buy_exam_request(db, current_user.id, exam_id)


# =====================================================
# OCHIQ TEST SANALARI VA RO'YXATDAN O'TISH
# =====================================================

@router.get("/sessions/open", response_model=List[ExamSessionResponse])
async def list_open_sessions(
    mock_exam_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await registration_service.list_open_sessions(db, mock_exam_id)


@router.post("/sessions/register", response_model=MockRegistrationResponse)
async def register_for_session(
    data: MockRegistrationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ochiq seansga yozilish. MK ID AYNAN shu yerda generatsiya qilinadi."""
    return await registration_service.register_for_session(db, current_user.id, data)


@router.get("/me/registrations", response_model=List[MockRegistrationResponse])
async def my_registrations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await registration_service.get_my_registrations(db, current_user.id)
