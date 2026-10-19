"""Buyurtmani tasdiqlash/rad etish — platforma admini shu yerdan o'tadi."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.billing.models import (
    BillingOrder, BillingOrderStatus, BillingProductType, BillingProduct, WritingAICreditGrant, TeacherReviewCreditGrant, SubscriptionPlan, UserSubscription,
)


async def lock_order(db: AsyncSession, order_id: str) -> BillingOrder:
    stmt = select(BillingOrder).where(BillingOrder.id == order_id)
    order = (await db.execute(stmt.with_for_update())).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Buyurtma topilmadi")
    return order


async def _claim_for_review(
    db: AsyncSession, order: BillingOrder, new_status: BillingOrderStatus,
    reviewer_id: uuid.UUID, note: str | None, now: datetime,
) -> None:
    """UNDER_REVIEW -> new_status o'tishini ATOMIK band qiladi.

    `with_for_update()` SQLite'da hech narsa qilmaydi, shuning uchun "Tasdiqlash"
    ikki marta (yoki ikki admin tomonidan) bosilsa ikkalasi ham eski holatni
    o'qib, obunani/xaridni ikki marta berishi mumkin edi. Shartli UPDATE esa
    bazaning o'zida yagona g'olibni aniqlaydi: ikkinchi so'rov rowcount=0 oladi.
    """
    result = await db.execute(
        update(BillingOrder)
        .where(BillingOrder.id == order.id, BillingOrder.status == BillingOrderStatus.UNDER_REVIEW)
        .values(status=new_status, reviewed_by=reviewer_id, reviewed_at=now, admin_note=note)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Buyurtma allaqachon ko'rib chiqilgan")


async def approve(db: AsyncSession, order: BillingOrder, reviewer_id: uuid.UUID, note: str | None) -> BillingOrder:
    if order.status != BillingOrderStatus.UNDER_REVIEW:
        raise HTTPException(409, "Faqat tekshiruvdagi buyurtmani tasdiqlash mumkin")
    if order.user_id == reviewer_id:
        # Pul nazorati: xaridor o'z to'lovini o'zi tasdiqlay olmaydi (admin ham).
        raise HTTPException(403, "O'zingizning buyurtmangizni o'zingiz tasdiqlay olmaysiz")
    now = datetime.now(timezone.utc)
    await _claim_for_review(db, order, BillingOrderStatus.PAID, reviewer_id, note, now)
    try:
        await _grant_entitlement(db, order, now)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    await db.refresh(order)
    return order


async def _grant_entitlement(db: AsyncSession, order: BillingOrder, now: datetime) -> None:
    if order.product_type == BillingProductType.SUBSCRIPTION:
        # Foydalanuvchi qatorini avval qulflaymiz: ikki tasdiqlash bir vaqtda o'qib, bir xil
        # boshlanish sanasini olib, ustma-ust obuna yaratmasin (enforce_practice_limit bilan bir xil usul).
        await db.execute(update(User).where(User.id == order.user_id).values(updated_at=User.updated_at))
        plan = await db.get(SubscriptionPlan, int(order.product_id or 0))
        if not plan:
            raise HTTPException(409, "Buyurtmaga bog‘langan tarif topilmadi")
        current = (await db.execute(select(UserSubscription).where(
            UserSubscription.user_id == order.user_id, UserSubscription.is_active.is_(True),
            UserSubscription.end_date > now).order_by(UserSubscription.end_date.desc()))).scalars().first()
        start = current.end_date if current else now
        db.add(UserSubscription(user_id=order.user_id, plan_id=plan.id, start_date=start,
                                end_date=start + timedelta(days=plan.duration_days), is_active=True))
    elif order.product_type == BillingProductType.WRITING_AI_CREDITS:
        # order_id UNIQUE makes grant idempotent even if a legacy/retried approval path runs twice.
        existing = (await db.execute(select(WritingAICreditGrant.id).where(WritingAICreditGrant.order_id == order.id))).scalar_one_or_none()
        if existing is None:
            product = await db.get(BillingProduct, int(order.product_id or 0))
            try:
                credits = int(product.external_ref or "0") if product else 0
            except ValueError:
                credits = 0
            if credits < 1:
                raise HTTPException(409, "Writing AI kredit mahsuloti noto‘g‘ri sozlangan")
            db.add(WritingAICreditGrant(user_id=order.user_id, order_id=order.id, credits_total=credits, credits_remaining=credits))
    elif order.product_type == BillingProductType.TEACHER_REVIEW_CREDITS:
        existing = (await db.execute(select(TeacherReviewCreditGrant.id).where(
            TeacherReviewCreditGrant.order_id == order.id
        ))).scalar_one_or_none()
        if existing is None:
            product = await db.get(BillingProduct, int(order.product_id or 0))
            try:
                credits = int(product.external_ref or "0") if product else 0
            except ValueError:
                credits = 0
            if credits < 1:
                raise HTTPException(409, "Teacher review mahsuloti noto‘g‘ri sozlangan")
            db.add(TeacherReviewCreditGrant(
                user_id=order.user_id, order_id=order.id,
                credits_total=credits, credits_remaining=credits,
            ))
    elif order.product_type in (BillingProductType.FULL_EXAM, BillingProductType.MOCK_EXAM):
        from app.modules.exams.mock.models import MockPurchase
        if not order.product_id:
            raise HTTPException(409, "Full Exam orderida imtihon ID yo‘q")
        purchase = (await db.execute(select(MockPurchase).where(
            MockPurchase.user_id == order.user_id, MockPurchase.mock_exam_id == order.product_id))).scalar_one_or_none()
        if purchase:
            purchase.is_active = True
        else:
            db.add(MockPurchase(user_id=order.user_id, mock_exam_id=order.product_id, is_active=True,
                                comment=f"{order.product_type.value.upper()} order {order.id}"))


async def reject(db: AsyncSession, order: BillingOrder, reviewer_id: uuid.UUID, note: str | None) -> BillingOrder:
    if order.status != BillingOrderStatus.UNDER_REVIEW:
        raise HTTPException(409, "Faqat tekshiruvdagi buyurtmani rad etish mumkin")
    await _claim_for_review(
        db, order, BillingOrderStatus.REJECTED, reviewer_id, note, datetime.now(timezone.utc)
    )
    await db.commit()
    await db.refresh(order)
    return order
