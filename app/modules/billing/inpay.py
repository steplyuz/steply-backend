"""inPAY API v2 integratsiyasi: to'lov yaratish, webhook imzosi va to'lov hodisalari.

Oqim:
1. Buyurtma (PENDING_PAYMENT) yaratiladi -> create_payment() inPAY'da to'lov ochadi, checkout_url qaytadi.
2. Xaridor checkout_url da to'laydi.
3. inPAY payment.succeeded webhook yuboradi -> imzo tekshiriladi -> buyurtma PAID bo'ladi va
   tarif/kredit avtomatik beriladi (admin tekshiruvisiz).

Xavfsizlik: webhook imzosi (HMAC-SHA256, t=.v1=) va 5 daqiqalik vaqt oynasi tekshiriladi;
to'langan summa buyurtma summasiga mos bo'lishi shart; qayta yuborilgan hodisa ikki marta bajarilmaydi.
"""
import hashlib
import hmac
import logging

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.billing.models import BillingOrder, BillingOrderStatus

logger = logging.getLogger(__name__)

PAYMENT_EVENTS_SUCCESS = "payment.succeeded"
PAYMENT_EVENTS_CANCELED = "payment.canceled"
MIN_AMOUNT_UZS = 1000


def is_enabled() -> bool:
    return bool(settings.INPAY_ENABLED and settings.INPAY_SECRET_KEY)


def verify_signature(raw: bytes, header: str, secret: str, now: int, tolerance: int) -> None:
    """X-Inpay-Signature: t=<unix>,v1=<hex> — ValueError agar yaroqsiz yoki eskirgan bo'lsa."""
    if not secret:
        raise ValueError("webhook sir sozlanmagan")
    fields = [p.strip() for p in (header or "").split(",") if "=" in p]
    t_values = [p.split("=", 1)[1] for p in fields if p.startswith("t=")]
    v1_values = [p.split("=", 1)[1] for p in fields if p.startswith("v1=")]
    if not t_values or not v1_values:
        raise ValueError("imzo sarlavhasi noto'g'ri")
    t = t_values[0]
    try:
        ts = int(t)
    except ValueError as exc:
        raise ValueError("vaqt belgisi noto'g'ri") from exc
    if abs(now - ts) > tolerance:
        raise ValueError("imzo muddati o'tgan")
    expected = hmac.new(secret.encode("utf-8"), f"{t}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, v) for v in v1_values):
        raise ValueError("imzo mos kelmadi")


async def create_payment(*, amount: int, description: str, order_id: str, idempotency_key: str) -> dict:
    """inPAY'da to'lov ochadi. Muvaffaqiyatli bo'lsa payment obyektini (id, checkout_url, ...) qaytaradi."""
    if not is_enabled():
        raise HTTPException(503, "Onlayn to'lov hozircha sozlanmagan")
    body = {
        "amount": int(amount),
        "currency": "UZS",
        "description": (description or "Steply buyurtmasi")[:200],
        "metadata": {"order_id": order_id},
    }
    if settings.INPAY_RETURN_URL:
        body["return_url"] = f"{settings.INPAY_RETURN_URL}?order={order_id}"
    if settings.INPAY_WEBHOOK_URL:
        body["webhook_url"] = settings.INPAY_WEBHOOK_URL
    headers = {
        "Authorization": f"Bearer {settings.INPAY_SECRET_KEY}",
        "Idempotency-Key": idempotency_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{settings.INPAY_API_BASE.rstrip('/')}/payments/", json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("inPAY ulanish xatosi: %s", exc)
        raise HTTPException(502, "To'lov tizimiga ulanib bo'lmadi. Keyinroq urinib ko'ring.")
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400 or not payload.get("success") or not payload.get("data", {}).get("checkout_url"):
        logger.error("inPAY to'lov yaratish xatosi: status=%s body=%s", resp.status_code, str(payload)[:500])
        raise HTTPException(502, "To'lov havolasini yaratib bo'lmadi. Keyinroq urinib ko'ring.")
    return payload["data"]


async def cancel_payment(payment_id: str) -> None:
    """Best-effort: inPAY'dagi to'lovni bekor qiladi. Xato bo'lsa faqat log qilinadi."""
    if not is_enabled() or not payment_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.INPAY_API_BASE.rstrip('/')}/payments/{payment_id}/cancel",
                headers={"Authorization": f"Bearer {settings.INPAY_SECRET_KEY}"},
            )
    except httpx.HTTPError:
        logger.warning("inPAY to'lovni bekor qilib bo'lmadi: %s", payment_id)


async def handle_event(db: AsyncSession, event: dict, now) -> str:
    """inPAY hodisasini qayta ishlaydi. Natija satri qaytadi (log/test uchun)."""
    from app.modules.billing.review import _grant_entitlement  # sikl importdan qochish

    etype = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}
    payment_id = str(obj.get("id") or "")
    if not payment_id or etype not in (PAYMENT_EVENTS_SUCCESS, PAYMENT_EVENTS_CANCELED):
        return "ignored"

    order = (await db.execute(
        select(BillingOrder).where(BillingOrder.provider_payment_id == payment_id).with_for_update()
    )).scalar_one_or_none()
    if order is None:
        logger.warning("inPAY: noma'lum to'lov %s", payment_id)
        return "unknown_payment"

    if etype == PAYMENT_EVENTS_CANCELED:
        await db.execute(
            update(BillingOrder)
            .where(BillingOrder.id == order.id, BillingOrder.status == BillingOrderStatus.PENDING_PAYMENT)
            .values(status=BillingOrderStatus.CANCELLED, admin_note="inPAY: to'lov bekor qilingan")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return "canceled"

    # payment.succeeded
    paid_amount = int(obj.get("amount") or 0)
    if paid_amount != int(order.amount):
        # Summa mos emas: tovar/tarif berilmaydi, admin xabardor bo'ladi.
        await db.execute(
            update(BillingOrder).where(BillingOrder.id == order.id)
            .values(admin_note=f"inPAY summa mos emas: to'langan {paid_amount}, kutilgan {order.amount}")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        logger.error("inPAY summa mos emas: order=%s paid=%s expected=%s", order.id, paid_amount, order.amount)
        return "amount_mismatch"

    claimed = await db.execute(
        update(BillingOrder)
        .where(
            BillingOrder.id == order.id,
            BillingOrder.status.in_((BillingOrderStatus.PENDING_PAYMENT, BillingOrderStatus.UNDER_REVIEW)),
        )
        .values(status=BillingOrderStatus.PAID, reviewed_at=now, admin_note="inPAY: payment.succeeded")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        current = await db.scalar(select(BillingOrder.status).where(BillingOrder.id == order.id))
        if current == BillingOrderStatus.PAID:
            # Takroriy webhook: allaqachon qayta ishlangan.
            await db.commit()
            return "already_processed"
        # Buyurtma bekor/rad etilgan, lekin pul olingan: avtomatik berilmaydi, admin hal qiladi.
        await db.execute(
            update(BillingOrder).where(BillingOrder.id == order.id)
            .values(admin_note=f"inPAY: to'landi, lekin buyurtma holati {current.value if current else '?'} — qo'lda tekshiring")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        logger.error("inPAY: to'langan buyurtma holati mos emas: order=%s status=%s", order.id, current)
        return "needs_review"
    try:
        await _grant_entitlement(db, order, now)
        await db.commit()
    except HTTPException:
        # Tarif/kredit berilmadi: buyurtma PAID bo'lmasin, inPAY qayta yuboradi.
        await db.rollback()
        raise
    return "paid"


async def fetch_payment(payment_id: str) -> dict | None:
    """inPAY'dan to'lov holatini to'g'ridan-to'g'ri so'raydi (webhook yetib bormagan bo'lsa zaxira yo'l)."""
    if not is_enabled() or not payment_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.INPAY_API_BASE.rstrip('/')}/payments/{payment_id}",
                headers={"Authorization": f"Bearer {settings.INPAY_SECRET_KEY}"},
            )
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("inPAY to'lov holatini olib bo'lmadi: %s", payment_id)
        return None
    if resp.status_code >= 400 or not payload.get("success"):
        return None
    return payload.get("data")


async def sync_order_payment(db: AsyncSession, order: BillingOrder, now) -> None:
    """Kutilayotgan onlayn buyurtmani inPAY holati bo'yicha yangilaydi (webhook'ga qaramasdan)."""
    if order.payment_provider != "inpay" or not order.provider_payment_id:
        return
    if order.status != BillingOrderStatus.PENDING_PAYMENT:
        return
    data = await fetch_payment(order.provider_payment_id)
    if not data:
        return
    status = data.get("status")
    if status == "succeeded":
        event_type = PAYMENT_EVENTS_SUCCESS
    elif status == "canceled":
        event_type = PAYMENT_EVENTS_CANCELED
    else:
        return
    try:
        await handle_event(db, {"type": event_type, "data": {"object": data}}, now)
    except HTTPException:
        logger.error("inPAY sinxronlash: buyurtma %s berilmadi", order.id)
