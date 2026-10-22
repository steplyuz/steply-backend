"""
content_access.py
=================
Reading / Listening test KONTENTIGA (qism, savol, variantlar, audio URL)
kirishni nazorat qiladi.

Avval `GET /reading-tests`, `/reading-tests/{id}`, `/listening-tests`,
`/listening-tests/{id}` hech qanday autentifikatsiyasiz va `is_active` /
`is_practice` / `is_free` filtrisiz ishlardi: pullik practice testlar,
nofaol testlar va mock imtihon savollari imtihondan oldin ko'rinib turardi.

Qoidalar:
  * ADMIN / SUPER_ADMIN                      -> hammasi
  * nofaol test                              -> 404
  * practice + is_free                       -> ochiq (anonim ham)
  * practice (pullik)                        -> kirgan foydalanuvchi, tarifi PLUS/PRO
  * mock imtihon testi (is_practice=False)   -> shu MockExam ga yozilgan talaba
                                                (Exams sessiyasi yoki oddiy foydalanuvchi)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.dependencies import get_current_user_optional
from app.modules.auth.models import User, UserRole
from app.modules.billing.entitlements import ensure_practice_access, get_user_tier
from app.modules.billing.models import SubscriptionTier
from app.modules.exams.auth.dependencies import get_current_exams_session

_STAFF_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)
_SKILL_FIELD = {
    "reading": "reading_id",
    "listening": "listening_id",
    "writing": "writing_id",
    "speaking": "speaking_id",
}


@dataclass
class ContentViewer:
    """Kontentni so'rayotgan shaxs (hech kim bo'lmasligi ham mumkin)."""

    user: User | None = None
    registration_id: int | None = None  # Exams sessiyasi bo'lsa

    @property
    def is_anonymous(self) -> bool:
        return self.user is None

    @property
    def is_staff(self) -> bool:
        return self.user is not None and self.user.global_role in _STAFF_ROLES


async def resolve_content_viewer(request: Request, db: AsyncSession) -> ContentViewer:
    """Avval Exams sessiyasini, so'ng oddiy foydalanuvchini aniqlaydi.

    Ikkala token turi ham `Authorization: Bearer` orqali kelishi mumkin,
    shuning uchun Exams tokeni yaroqsiz bo'lsa oddiy tokenga o'tiladi.
    """
    try:
        session = await get_current_exams_session(request, db)
    except HTTPException:
        session = None
    if session is not None and session.registration_id is not None:
        return ContentViewer(user=session.user, registration_id=session.registration_id)

    user = await get_current_user_optional(request, db)
    return ContentViewer(user=user)


def _skill_field(skill: str) -> str:
    try:
        return _SKILL_FIELD[skill]
    except KeyError as exc:  # dasturchi xatosi, foydalanuvchi emas
        raise ValueError(f"Noma'lum skill: {skill}") from exc


async def mock_exam_ids_for_test(db: AsyncSession, test_id: str, skill: str) -> list[str]:
    """Berilgan test qaysi MockExam'larga biriktirilgan."""
    from app.modules.exams.mock.models import MockExam

    column = getattr(MockExam, _skill_field(skill))
    rows = await db.execute(select(MockExam.id).where(column == test_id))
    return list(rows.scalars().all())


async def registration_test_id(db: AsyncSession, registration_id: int, skill: str) -> str | None:
    """Nomzod registratsiyasiga tegishli MockExam'ning shu skill testi (bekor qilinmagan bo'lsa)."""
    from app.modules.exams.mock.models import (
        MockExam,
        MockRegistration,
        RegistrationStatus,
    )

    reg = await db.get(MockRegistration, registration_id)
    if reg is None or reg.status == RegistrationStatus.CANCELLED:
        return None
    exam = await db.get(MockExam, reg.mock_exam_id)
    if exam is None:
        return None
    return getattr(exam, _skill_field(skill), None)


async def authorize_test_content(
    db: AsyncSession, viewer: ContentViewer, test: Any, skill: str
) -> None:
    """Testni ko'rishga ruxsat bo'lmasa HTTPException ko'taradi."""
    if viewer.is_staff:
        return

    not_found = HTTPException(404, "Test topilmadi")
    if not getattr(test, "is_active", True):
        raise not_found

    is_practice = bool(getattr(test, "is_practice", False))
    is_free = bool(getattr(test, "is_free", False))

    # Platforma ichidagi bepul practice - ochiq katalog.
    if is_practice and is_free:
        return

    # Exams sessiyasi: faqat o'z MockExam'idagi test.
    if viewer.registration_id is not None:
        if await registration_test_id(db, viewer.registration_id, skill) == test.id:
            return
        raise not_found

    if viewer.user is None:
        raise HTTPException(401, "Test kontentini ko'rish uchun tizimga kiring")

    user = viewer.user

    if is_practice:
        # FREE tarif: faqat is_free; pullik tarif: ruxsat. 403 xabari to'g'ridan-to'g'ri qaytadi.
        await ensure_practice_access(db, user.id, test, skill.upper())
        return

    # Mock imtihon testi: foydalanuvchi shu MockExam'ga yozilgan bo'lishi kerak.
    from app.modules.exams.mock.services import has_active_enrollment

    for exam_id in await mock_exam_ids_for_test(db, test.id, skill):
        if await has_active_enrollment(db, user.id, exam_id):
            return
    raise not_found


async def filter_viewable(db: AsyncSession, viewer: ContentViewer, tests: list, skill: str) -> list:
    """Ro'yxatdan faqat viewer ko'ra oladigan testlarni qoldiradi (writing/speaking katalogi uchun)."""
    out = []
    for test in tests:
        try:
            await authorize_test_content(db, viewer, test, skill)
        except HTTPException:
            continue
        out.append(test)
    return out


async def visible_catalog_filter(
    db: AsyncSession, viewer: ContentViewer, skill: str
) -> dict[str, Any]:
    """Ro'yxat endpointi uchun servisga beriladigan filtr parametrlari.

    Ro'yxat to'liq savollar daraxtini qaytargani uchun u faqat ko'rishga
    ruxsat bor testlarni o'z ichiga olishi kerak.
    """
    if viewer.is_staff:
        return {}
    if viewer.registration_id is not None:
        tid = await registration_test_id(db, viewer.registration_id, skill)
        return {"only_ids": [tid] if tid else [], "is_active": True}
    base = {"is_active": True, "is_practice": True}
    if viewer.user is None:
        return {**base, "is_free": True}
    tier = await get_user_tier(db, viewer.user.id)
    if tier == SubscriptionTier.FREE:
        return {**base, "is_free": True}
    return base
