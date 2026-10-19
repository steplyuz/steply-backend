"""Authoritative promo validation, redemption and discount calculation."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import BillingOrder, PromoCode, PromoCodeUsage




def calculate_discount(base_amount: int, *, discount_percent: int | None, discount_amount: int | None) -> int:
    """Return a non-negative integer currency discount using Decimal only."""
    if base_amount < 0:
        raise ValueError("base_amount must be non-negative")
    if discount_percent is not None and discount_amount is not None:
        raise HTTPException(409, "Promo kodida foiz va summa chegirmasi bir vaqtda berilmagan bo'lishi kerak")
    if discount_percent is not None:
        if not 0 <= discount_percent <= 100:
            raise HTTPException(409, "Promo chegirmasi 0..100 foiz oralig'ida bo'lishi kerak")
        raw = Decimal(base_amount) * Decimal(discount_percent) / Decimal(100)
    else:
        raw = Decimal(discount_amount or 0)
    discount = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return min(max(discount, 0), base_amount)


async def validate_promo(db: AsyncSession, code: str, *, user_id=None, now: datetime | None = None) -> PromoCode:
    now = now or datetime.now(timezone.utc)
    normalized = code.strip().upper()
    promo = (
        await db.execute(
            select(PromoCode).where(
                PromoCode.code == normalized,
                PromoCode.is_active.is_(True),
                or_(PromoCode.valid_from.is_(None), PromoCode.valid_from <= now),
                or_(PromoCode.valid_until.is_(None), PromoCode.valid_until >= now),
            )
        )
    ).scalar_one_or_none()
    if promo is None:
        raise HTTPException(400, "Promo kod yaroqsiz, faol emas yoki muddati tugagan")
    if promo.max_usage is not None and promo.used_count >= promo.max_usage:
        raise HTTPException(409, "Promo koddan foydalanish limiti tugagan")
    if user_id is not None:
        used = (await db.execute(
            select(PromoCodeUsage.id).where(
                PromoCodeUsage.promo_code_id == promo.id, PromoCodeUsage.user_id == user_id
            ).limit(1)
        )).scalar_one_or_none()
        if used is not None:
            raise HTTPException(409, "Bu promo kodni siz allaqachon ishlatgansiz")
    return promo


async def redeem_promo(
    db: AsyncSession,
    *,
    user_id,
    code: str,
    base_amount: int,
    order: BillingOrder | None = None,
) -> tuple[PromoCode, int]:
    """Atomically reserve one promo use and return the authoritative discount."""
    normalized = code.strip().upper()
    now = datetime.now(timezone.utc)

    promo = await validate_promo(db, normalized, user_id=user_id, now=now)
    discount = calculate_discount(
        base_amount,
        discount_percent=promo.discount_percent,
        discount_amount=promo.discount_amount,
    )

    # Atomic usage increment prevents max_usage=1 from being redeemed twice.
    conditions = [
        PromoCode.id == promo.id,
        PromoCode.is_active.is_(True),
        or_(PromoCode.valid_from.is_(None), PromoCode.valid_from <= now),
        or_(PromoCode.valid_until.is_(None), PromoCode.valid_until >= now),
    ]
    if promo.max_usage is not None:
        conditions.append(PromoCode.used_count < promo.max_usage)
    result = await db.execute(
        update(PromoCode)
        .where(*conditions)
        .values(used_count=PromoCode.used_count + 1)
        # SQLite returns naive datetimes; skip the ORM's in-Python evaluator
        # (it would compare them with the aware `now` and raise TypeError).
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(409, "Promo koddan foydalanish limiti tugagan")
    await db.refresh(promo, ["used_count"])

    usage = PromoCodeUsage(promo_code_id=promo.id, user_id=user_id)
    db.add(usage)
    if order is not None:
        order.original_amount = base_amount
        order.amount = base_amount - discount
        order.discount_amount = discount
        order.promo_code_id = promo.id
    try:
        await db.flush()
    except IntegrityError as exc:
        # Unique(promo_code_id,user_id) makes per-user redemption idempotently
        # exclusive; the surrounding transaction rolls back the usage increment.
        raise HTTPException(409, "Bu promo kodni siz allaqachon ishlatgansiz") from exc
    return promo, discount


async def release_promo(db: AsyncSession, order: BillingOrder) -> bool:
    """Bekor qilingan buyurtmaning promo ishlatilishini qaytaradi.

    `redeem_promo` buyurtma yaratilganda limitdan 1 ta sarflaydi; buyurtma
    CANCELLED bo'lsa, o'sha foydalanuvchi uchun ham, umumiy limit uchun ham
    qaytarilmasa, promo "yonib" ketardi.
    """
    if order.promo_code_id is None:
        return False
    deleted = await db.execute(
        delete(PromoCodeUsage).where(
            PromoCodeUsage.promo_code_id == order.promo_code_id,
            PromoCodeUsage.user_id == order.user_id,
        )
    )
    if (deleted.rowcount or 0) > 0:
        await db.execute(
            update(PromoCode)
            .where(PromoCode.id == order.promo_code_id, PromoCode.used_count > 0)
            .values(used_count=PromoCode.used_count - 1)
            .execution_options(synchronize_session=False)
        )
    return True
