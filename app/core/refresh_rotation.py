"""Refresh token rotatsiyasi — `auth` va `exams_auth` uchun yagona mantiq.

Qoidalar (ikkala servisda bir xil):
- Eski refresh token faqat BIR marta sarflanadi: shartli UPDATE (`claim_refresh_token`) atomik gate.
  SQLite'da SELECT ... FOR UPDATE ishlamaydi, Postgres'da esa qo'shimcha himoya.
- Qayta ishlatilgan (revoked) token: agar oxirgi rotatsiyadan `REFRESH_REUSE_GRACE_SECONDS` o'tmagan
  bo'lsa — faqat rad etiladi, sessiya oilasi saqlanadi (parallel tab: yangi token allaqachon cookie'da).
  Grace'dan keyin — o'g'irlangan token ehtimoli: butun oila bekor qilinadi.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

REFRESH_REUSE_GRACE_SECONDS = 30


class ReuseOutcome(str, Enum):
    GRACE = "grace"                    # parallel so'rov: faqat rad etiladi
    FAMILY_REVOKED = "family_revoked"  # o'g'irlangan token ehtimoli: oila bekor qilindi


def aware(dt: datetime) -> datetime:
    """SQLite timezone'siz qaytaradi — UTC deb hisoblaymiz."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def within_reuse_grace(updated_at: datetime, now: datetime) -> bool:
    return (now - aware(updated_at)).total_seconds() <= REFRESH_REUSE_GRACE_SECONDS


async def claim_refresh_token(db: AsyncSession, model: Any, session_id, refresh_hash: str, now: datetime) -> bool:
    """Eski tokenni atomik sarflaydi. True — shu so'rov yutdi."""
    result = await db.execute(
        update(model)
        .where(
            model.id == session_id,
            model.refresh_token_hash == refresh_hash,
            model.is_revoked.is_(False),
            model.expires_at >= now,
        )
        .values(is_revoked=True)
        # Naive/aware datetime nomuvofiqligi tufayli ORM Python'da sinxronlamasin: bazadagi qator asosiy manba.
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def revoke_family(db: AsyncSession, model: Any, family_id) -> None:
    await db.execute(
        update(model)
        .where(model.session_family_id == family_id)
        .values(is_revoked=True)
        .execution_options(synchronize_session=False)
    )


async def _fresh_updated_at(db: AsyncSession, model: Any, session_id) -> datetime:
    """Yutgan so'rov qatorni yangilagan: ORM snapshot eski, shuning uchun bazadan o'qiymiz."""
    return (await db.execute(select(model.updated_at).where(model.id == session_id))).scalar_one()


async def on_reused_token(db: AsyncSession, model: Any, session: Any, now: datetime) -> ReuseOutcome:
    """Bekor qilingan (revoked) token qayta kelganda."""
    if within_reuse_grace(session.updated_at, now):
        return ReuseOutcome.GRACE
    await revoke_family(db, model, session.session_family_id)
    await db.commit()
    return ReuseOutcome.FAMILY_REVOKED


async def on_lost_claim(db: AsyncSession, model: Any, session: Any, now: datetime) -> ReuseOutcome:
    """`claim_refresh_token` yutqazilganda (parallel so'rov oldin yutgan)."""
    fresh = await _fresh_updated_at(db, model, session.id)
    if within_reuse_grace(fresh, now):
        await db.rollback()
        return ReuseOutcome.GRACE
    await revoke_family(db, model, session.session_family_id)
    await db.commit()
    return ReuseOutcome.FAMILY_REVOKED
