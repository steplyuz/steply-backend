import json
from typing import Optional
from uuid import UUID
from app.core.paths import PRIVATE_DIR
from app.core.internal_auth import verify_internal_token
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import FileResponse
from app.core.file_storage import ensure_private_file, generated_name, private_path, safe_filename_for_header
from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User, UserRole
from app.modules.billing.models import (
    BillingOrder, BillingOrderStatus, BillingProduct, BillingProductType,
    PromoCode, SubscriptionPlan, SubscriptionTier, UserSubscription,
)
from app.modules.billing import entitlements
from app.modules.billing.promo import calculate_discount, redeem_promo, release_promo, validate_promo
from app.modules.billing.response_schemas import (
    LimitsPublic, OrderPublic, PlanPublic, ProductPublic, PromoCodePublic, SubscriptionPublic,
)

router = APIRouter(tags=["Billing"])
admin_router = APIRouter(prefix="/admin/billing", tags=["Admin Billing"])
RECEIPT_DIR = PRIVATE_DIR / "billing_receipts"
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf", "image/webp": ".webp"}
MAX_BYTES = 8 * 1024 * 1024

async def _notify_telegram_billing_admin(order: BillingOrder, file_path) -> None:
    """Chekni Telegram admin chatiga yuboradi; xato bo‘lsa foydalanuvchi uploadini bekor qilmaydi."""
    from app.core.config import settings
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_BILLING_ADMIN_CHAT_ID:
        return
    import httpx
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Tasdiqlash", "callback_data": f"bill:approve:{order.id}"},
        {"text": "❌ Rad etish", "callback_data": f"bill:reject:{order.id}"},
    ]]}
    caption = (f"💳 Steply to‘lov cheki\nBuyurtma: {order.id}\nMahsulot: {order.product_title}\n"
               f"Summa: {order.amount:,} UZS\nHolat: tekshiruvda")[:1000]
    endpoint = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            with open(file_path, "rb") as receipt:
                response = await client.post(endpoint, data={"chat_id": settings.TELEGRAM_BILLING_ADMIN_CHAT_ID,
                    "caption": caption, "reply_markup": json.dumps(keyboard)},
                    files={"document": (file_path.name, receipt, "application/octet-stream")})
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError("Telegram Bot API returned ok=false")
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Telegram billing notification failed for order %s", order.id)

# Fayl magic bytes (imzo) tekshiruvi — content-type header soxta bo'lishi mumkin
_MAGIC: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"RIFF", ".webp"),  # WebP: RIFF....WEBP
    (b"%PDF", ".pdf"),
]

def _ext_from_magic(data: bytes) -> str | None:
    for sig, ext in _MAGIC:
        if data[:len(sig)] == sig:
            # WebP uchun qo'shimcha tekshiruv
            if ext == ".webp" and data[8:12] != b"WEBP":
                continue
            return ext
    return None

class OrderCreate(BaseModel):
    product_type: BillingProductType
    product_id: str | None = Field(default=None, max_length=100)
    user_note: str | None = Field(default=None, max_length=1000)
    promo_code: str | None = Field(default=None, max_length=50)

    @field_validator("promo_code", mode="before")
    @classmethod
    def clean_promo(cls, v):
        if v is None:
            return None
        cleaned = str(v).strip()
        if not cleaned:
            return None
        return cleaned.upper()

class ProductCreate(BaseModel):
    product_type: BillingProductType
    code: str = Field(min_length=2, max_length=100, pattern=r"^[A-Z0-9][A-Z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price: int = Field(gt=0, le=100_000_000)
    external_ref: str | None = Field(default=None, max_length=100)
    is_active: bool = True

class ProductUpdate(BaseModel):
    """PATCH for an existing billing product; product type is immutable."""
    code: str | None = Field(default=None, min_length=2, max_length=100, pattern=r"^[A-Z0-9][A-Z0-9_.-]*$")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price: int | None = Field(default=None, gt=0, le=100_000_000)
    external_ref: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self):
        for field in self.model_fields_set - {"description", "external_ref"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} null bo'lishi mumkin emas")
        return self


class ReviewBody(BaseModel):
    note: str | None = Field(default=None, max_length=1000)

class PlanCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    price: int = Field(gt=0, le=100_000_000)
    duration_days: int = Field(gt=0, le=3650)
    tier: SubscriptionTier
    ai_limit: int = Field(default=0, ge=0)
    exam_limit: int = Field(default=0, ge=0)
    included_skills: list[str] = Field(default_factory=lambda: ["READING", "LISTENING", "WRITING"])
    ai_enabled: bool = True
    discount_percent: int = Field(default=0, ge=0, le=100)
    is_active: bool = True

    @field_validator("included_skills")
    @classmethod
    def validate_skills(cls, value):
        allowed = {"READING", "LISTENING", "WRITING", "SPEAKING"}
        cleaned = list(dict.fromkeys(str(v).upper() for v in value))
        if not cleaned or set(cleaned) - allowed:
            raise ValueError("included_skills faqat READING, LISTENING, WRITING, SPEAKING bo‘lishi kerak")
        return cleaned


class PromoCodeCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    discount_percent: int | None = Field(default=None, ge=0, le=100)
    discount_amount: int | None = Field(default=None, ge=0, le=100_000_000)
    max_usage: int | None = Field(default=None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _validate_discount(self):
        values = self.model_dump()
        if values.get("discount_percent") is None and values.get("discount_amount") is None:
            raise ValueError("discount_percent yoki discount_amount dan biri kerak")
        if values.get("discount_percent") is not None and values.get("discount_amount") is not None:
            raise ValueError("discount_percent va discount_amount bir vaqtda berilmaydi")
        if values.get("valid_from") and values.get("valid_until") and values["valid_from"] > values["valid_until"]:
            raise ValueError("valid_until valid_from dan oldin bo'lishi mumkin emas")
        return self


class PlanUpdate(BaseModel):
    """PATCH uchun: faqat yuborilgan maydonlar o'zgaradi (PlanCreate defaultlariga qaytmaydi)."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    price: int | None = Field(default=None, gt=0, le=100_000_000)
    duration_days: int | None = Field(default=None, gt=0, le=3650)
    tier: SubscriptionTier | None = None
    ai_limit: int | None = Field(default=None, ge=0)
    exam_limit: int | None = Field(default=None, ge=0)
    included_skills: list[str] | None = None
    ai_enabled: bool | None = None
    discount_percent: int | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None

    @field_validator("included_skills")
    @classmethod
    def validate_skills(cls, value):
        if value is None:
            return value
        allowed = {"READING", "LISTENING", "WRITING", "SPEAKING"}
        cleaned = list(dict.fromkeys(str(v).upper() for v in value))
        if not cleaned or set(cleaned) - allowed:
            raise ValueError("included_skills faqat READING, LISTENING, WRITING, SPEAKING bo‘lishi kerak")
        return cleaned

    @model_validator(mode="after")
    def _no_null_for_required(self):
        # `description` aniq null bilan tozalanishi mumkin, qolganlari — yo'q.
        for field in self.model_fields_set - {"description"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} null bo'lishi mumkin emas")
        return self


class PromoCodeUpdate(BaseModel):
    """PATCH uchun: faqat yuborilgan maydonlar o'zgaradi.

    `max_usage`, `valid_from`, `valid_until` aniq null bilan tozalanishi mumkin
    (cheklovsiz / muddatsiz).
    """

    code: str | None = Field(default=None, min_length=2, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    discount_percent: int | None = Field(default=None, ge=0, le=100)
    discount_amount: int | None = Field(default=None, ge=0, le=100_000_000)
    max_usage: int | None = Field(default=None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self):
        nullable = {"discount_percent", "discount_amount", "max_usage", "valid_from", "valid_until"}
        for field in self.model_fields_set - nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field} null bo'lishi mumkin emas")
        return self


class PromoValidateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    amount: int = Field(gt=0, le=100_000_000)


@router.get("/billing/plans", response_model=list[PlanPublic])
async def list_plans(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True)).order_by(SubscriptionPlan.price))).scalars().all()
    return rows

@router.get("/billing/products", response_model=list[ProductPublic])
async def list_products(product_type: BillingProductType | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(BillingProduct).where(BillingProduct.is_active.is_(True)).order_by(BillingProduct.price)
    if product_type is not None:
        stmt = stmt.where(BillingProduct.product_type == product_type)
    return (await db.execute(stmt)).scalars().all()


@router.post("/billing/promo/validate")
async def validate_promo_code(data: PromoValidateRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    promo = await validate_promo(db, data.code, user_id=user.id)
    discount = calculate_discount(data.amount, discount_percent=promo.discount_percent, discount_amount=promo.discount_amount)
    return {"code": promo.code, "discount_amount": discount, "final_amount": data.amount - discount,
            "discount_percent": promo.discount_percent, "fixed_discount_amount": promo.discount_amount}


@router.post("/billing/orders", status_code=201, response_model=OrderPublic)
async def create_order(data: OrderCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    original_price: int | None = None
    if data.product_type == BillingProductType.SUBSCRIPTION:
        try:
            plan_id = int(data.product_id or "")
        except ValueError:
            raise HTTPException(422, "Obuna uchun product_id tarif ID bo‘lishi kerak")
        plan = await db.get(SubscriptionPlan, plan_id)
        if not plan or not plan.is_active or plan.tier not in (SubscriptionTier.PLUS, SubscriptionTier.PRO):
            raise HTTPException(404, "Faol Plus/Pro tarif topilmadi")
        from app.modules.billing.promo import calculate_discount
        original_price = int(plan.price)
        amount = plan.price - calculate_discount(plan.price, discount_percent=plan.discount_percent, discount_amount=None)
        title, product_id = plan.name, str(plan.id)
    elif data.product_type == BillingProductType.WRITING_AI_CREDITS:
        try:
            product_id = int(data.product_id or "")
        except ValueError:
            raise HTTPException(422, "Writing AI krediti uchun product_id katalog ID bo‘lishi kerak")
        product = await db.get(BillingProduct, product_id)
        if not product or not product.is_active or product.product_type != BillingProductType.WRITING_AI_CREDITS:
            raise HTTPException(404, "Faol Writing AI kredit mahsuloti topilmadi")
        try:
            credits = int(product.external_ref or "0")
        except ValueError:
            credits = 0
        if credits < 1:
            raise HTTPException(409, "Writing AI mahsulotida external_ref kreditlar soni sifatida sozlanmagan")
        amount, title, product_id = product.price, product.title, str(product.id)
    elif data.product_type == BillingProductType.TEACHER_REVIEW_CREDITS:
        try:
            product_id = int(data.product_id or "")
        except ValueError:
            raise HTTPException(422, "Teacher review uchun product_id katalog ID bo‘lishi kerak")
        product = await db.get(BillingProduct, product_id)
        if not product or not product.is_active or product.product_type != BillingProductType.TEACHER_REVIEW_CREDITS:
            raise HTTPException(404, "Faol teacher review mahsuloti topilmadi")
        try:
            credits = int(product.external_ref or "0")
        except ValueError:
            credits = 0
        if credits < 1:
            raise HTTPException(409, "Teacher review mahsulotida external_ref tekshiruvlar soni sifatida sozlanmagan")
        amount, title, product_id = product.price, product.title, str(product.id)
    elif data.product_type == BillingProductType.FULL_EXAM:
        try:
            catalog_id = int(data.product_id or "")
        except ValueError:
            raise HTTPException(422, "Full Exam uchun product_id katalog ID bo‘lishi kerak")
        product = await db.get(BillingProduct, catalog_id)
        if not product or not product.is_active or product.product_type != BillingProductType.FULL_EXAM:
            raise HTTPException(404, "Faol Full Exam mahsuloti topilmadi")
        if not product.external_ref:
            raise HTTPException(409, "Full Exam mahsuloti MockExam bilan bog‘lanmagan")
        from app.modules.exams.mock.models import MockExam
        exam = await db.get(MockExam, product.external_ref)
        if not exam or not exam.is_active:
            raise HTTPException(409, "Full Exam mahsulotining imtihoni topilmadi")
        amount, title, product_id = product.price, product.title, product.external_ref
    elif data.product_type == BillingProductType.MOCK_EXAM:
        # Mock Exam narxi va ID'si MockExam katalogidan olinadi. BillingProduct
        # ichidagi FULL_EXAM katalogi bu oqim uchun shart emas.
        exam_id = (data.product_id or "").strip()
        if not exam_id:
            raise HTTPException(422, "Mock Exam uchun product_id imtihon ID'si bo‘lishi kerak")
        from app.modules.exams.mock.models import MockExam, MockPurchase
        exam = await db.get(MockExam, exam_id)
        if not exam or not exam.is_active:
            raise HTTPException(404, "Faol Mock Exam topilmadi")
        if int(exam.price or 0) <= 0:
            raise HTTPException(422, "Bepul Mock Exam uchun /mock-exams/{id}/buy endpointidan foydalaning")
        owned = await db.scalar(select(MockPurchase.id).where(
            MockPurchase.user_id == user.id,
            MockPurchase.mock_exam_id == exam.id,
            MockPurchase.is_active.is_(True),
        ).limit(1))
        if owned is not None:
            raise HTTPException(409, "Siz bu Mock Examni allaqachon sotib olgansiz")
        pending = await db.scalar(select(BillingOrder).where(
            BillingOrder.user_id == user.id,
            BillingOrder.product_type == BillingProductType.MOCK_EXAM,
            BillingOrder.product_id == exam.id,
            BillingOrder.status.in_((BillingOrderStatus.PENDING_PAYMENT, BillingOrderStatus.UNDER_REVIEW)),
        ).order_by(BillingOrder.created_at.desc()).limit(1))
        if pending is not None:
            # Takroriy click/request yangi buyurtma yoki chegirma sarfi yaratmaydi.
            return pending
        amount, title, product_id = int(exam.price), exam.title, exam.id
    else:
        raise HTTPException(410, "Ushbu mahsulot turi eskirgan. Faqat PLUS/PRO subscription, FULL_EXAM, WRITING_AI_CREDITS yoki TEACHER_REVIEW_CREDITS xaridi mavjud.")
    if original_price is None:
        original_price = int(amount)
    order = BillingOrder(
        user_id=user.id, product_type=data.product_type, product_id=product_id,
        product_title=title, amount=amount, original_amount=original_price,
        discount_amount=max(0, original_price - amount), user_note=data.user_note,
    )
    db.add(order)
    await db.flush()
    if data.promo_code:
        await redeem_promo(db, user_id=user.id, code=data.promo_code, base_amount=amount, order=order)
        # Hisobotda tarif chegirmasi va promo chegirmasi birgalikda ko‘rinadi.
        order.original_amount = original_price
        order.discount_amount = max(0, original_price - order.amount)
    await db.commit(); await db.refresh(order)
    return order

@router.post("/billing/orders/{order_id}/cancel")
async def cancel_order(order_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Foydalanuvchi o'z buyurtmasini bekor qiladi (chek yuborilmagan yoki rad etilgan bo'lsa).

    Promo kod ishlatilgan bo'lsa, u qaytariladi. Tekshiruvdagi (UNDER_REVIEW)
    va to'langan buyurtmalar bekor qilinmaydi.
    """
    order = await db.get(BillingOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "Buyurtma topilmadi")
    result = await db.execute(
        update(BillingOrder)
        .where(
            BillingOrder.id == order_id,
            BillingOrder.user_id == user.id,
            BillingOrder.status.in_((BillingOrderStatus.PENDING_PAYMENT, BillingOrderStatus.REJECTED)),
        )
        .values(status=BillingOrderStatus.CANCELLED)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Bu buyurtmani bekor qilib bo'lmaydi")
    await release_promo(db, order)
    await db.commit()
    await db.refresh(order)
    return order

@router.post("/billing/orders/{order_id}/receipt", status_code=202)
async def upload_receipt(order_id: str, file: UploadFile = File(...), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    order = await db.get(BillingOrder, order_id)
    if not order or order.user_id != user.id: raise HTTPException(404, "Buyurtma topilmadi")
    if order.status not in (BillingOrderStatus.PENDING_PAYMENT, BillingOrderStatus.REJECTED): raise HTTPException(409, "Bu buyurtma uchun chek yuborib bo‘lmaydi")
    ext = ALLOWED.get(file.content_type or "")
    if not ext: raise HTTPException(415, "Chek JPG, PNG, WEBP yoki PDF bo‘lishi kerak")
    content = await file.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES: raise HTTPException(413, "Chek hajmi 8 MB dan oshmasligi kerak")
    real_ext = _ext_from_magic(content)
    if not real_ext or real_ext != ext:
        raise HTTPException(415, "Fayl tarkibi e'lon qilingan turga mos kelmadi")
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    old_key = order.receipt_storage_key
    name = generated_name(ext)
    path = private_path("billing_receipts", name)
    import anyio
    await anyio.to_thread.run_sync(path.write_bytes, content)
    order.receipt_storage_key = name
    order.receipt_path = f"/billing/orders/{order.id}/receipt/file"
    order.status = BillingOrderStatus.UNDER_REVIEW
    order.admin_note = None
    try:
        await db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    await db.refresh(order)
    if old_key and old_key != name:
        old_path = private_path("billing_receipts", old_key)
        old_path.unlink(missing_ok=True)
    await _notify_telegram_billing_admin(order, path)
    return {"id": order.id, "status": order.status, "message": "Chek admin tekshiruviga yuborildi"}

@router.get("/billing/orders/{order_id}/receipt/file", summary="Private payment receipt")
async def download_my_receipt(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(BillingOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "Buyurtma topilmadi")
    if not order.receipt_path:
        raise HTTPException(404, "Chek topilmadi")
    if not order.receipt_storage_key:
        raise HTTPException(404, "Chek topilmadi")
    name = order.receipt_storage_key
    path = ensure_private_file(private_path("billing_receipts", name), "billing_receipts")
    return FileResponse(path, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{safe_filename_for_header(name, "receipt")}"'})


@admin_router.get("/orders/{order_id}/receipt/file", summary="Admin payment receipt")
async def admin_download_receipt(
    order_id: str,
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(BillingOrder, order_id)
    if not order or not order.receipt_path:
        raise HTTPException(404, "Chek topilmadi")
    if not order.receipt_storage_key:
        raise HTTPException(404, "Chek topilmadi")
    name = order.receipt_storage_key
    path = ensure_private_file(private_path("billing_receipts", name), "billing_receipts")
    return FileResponse(path, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{safe_filename_for_header(name, "receipt")}"'})


@router.get("/billing/orders", response_model=list[OrderPublic])
async def my_orders(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BillingOrder).where(BillingOrder.user_id == user.id).order_by(BillingOrder.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return rows

@router.get("/billing/orders/{order_id}", response_model=OrderPublic)
async def order_detail(order_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    order = await db.get(BillingOrder, order_id)
    if not order or order.user_id != user.id: raise HTTPException(404, "Buyurtma topilmadi")
    return order

@router.get("/billing/me/subscription", response_model=Optional[SubscriptionPublic])
async def my_subscription(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    result = await db.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active.is_(True), UserSubscription.start_date <= now, UserSubscription.end_date > now).order_by(UserSubscription.end_date.desc()))
    return result.scalars().first()

@router.post("/billing/me/subscription/cancel")
async def cancel_my_subscription(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Foydalanuvchi o'z faol obunasini bekor qiladi."""
    now = datetime.now(timezone.utc)
    sub = (
        await db.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_id == user.id,
                UserSubscription.is_active.is_(True),
                UserSubscription.start_date <= now,
                UserSubscription.end_date > now,
            )
            .order_by(UserSubscription.end_date.desc())
            .limit(1)
        )
    ).scalars().first()
    if not sub:
        raise HTTPException(404, "Faol obuna topilmadi")
    sub.is_active = False
    await db.commit()
    return {"message": "Faol obuna muvaffaqiyatli bekor qilindi", "is_active": False}

@router.get("/billing/me/teacher-review-credits")
async def my_teacher_review_credits(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Foydalanuvchining sotib olingan va hali sarflanmagan teacher tekshiruvlari."""
    from app.modules.billing.models import TeacherReviewCreditGrant
    grants = (await db.execute(select(TeacherReviewCreditGrant.credits_remaining).where(
        TeacherReviewCreditGrant.user_id == user.id,
        TeacherReviewCreditGrant.credits_remaining > 0,
    ))).scalars().all()
    return {"remaining": sum(int(value) for value in grants)}


@router.get("/billing/me/limits", response_model=LimitsPublic)
async def my_limits(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Joriy tarif, barcha skilllar uchun umumiy practice kvotasi va Analysis kvotasi.

    Frontend limitlarni qotirib yozmaydi; ishlatilgan/qolgan miqdorlar backenddan olinadi.
    """
    return await entitlements.get_remaining_limits(db, user.id)

@admin_router.get("/plans", response_model=list[PlanPublic])
async def admin_plans(_: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.id))).scalars().all()

@admin_router.post("/plans", status_code=201)
async def admin_create_plan(data: PlanCreate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    if data.tier == SubscriptionTier.FREE:
        raise HTTPException(422, "FREE tarif sotuv mahsuloti sifatida yaratilmaydi")
    exists = (await db.execute(select(SubscriptionPlan.id).where(SubscriptionPlan.name == data.name))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Bu tarif nomi allaqachon mavjud")
    plan = SubscriptionPlan(**data.model_dump())
    db.add(plan); await db.commit(); await db.refresh(plan)
    return plan

@admin_router.patch("/plans/{plan_id}")
async def admin_update_plan(plan_id: int, data: PlanUpdate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    plan = await db.get(SubscriptionPlan, plan_id)
    if not plan: raise HTTPException(404, "Tarif topilmadi")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("tier") == SubscriptionTier.FREE:
        raise HTTPException(422, "FREE tarif sotuv mahsuloti sifatida saqlanmaydi")
    if "name" in changes:
        duplicate = (await db.execute(select(SubscriptionPlan.id).where(SubscriptionPlan.name == changes["name"], SubscriptionPlan.id != plan_id))).scalar_one_or_none()
        if duplicate: raise HTTPException(409, "Bu tarif nomi allaqachon mavjud")
    for k, v in changes.items(): setattr(plan, k, v)
    await db.commit(); await db.refresh(plan)
    return plan

@admin_router.get("/promo-codes", response_model=list[PromoCodePublic])
async def admin_list_promo_codes(_: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(PromoCode).order_by(PromoCode.id.desc()))).scalars().all()

@admin_router.post("/promo-codes", status_code=201)
async def admin_create_promo(data: PromoCodeCreate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    code = data.code.strip().upper()
    exists = (await db.execute(select(PromoCode.id).where(PromoCode.code == code))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Bu promo kod allaqachon mavjud")
    promo = PromoCode(**data.model_dump(exclude={"code"}), code=code)
    db.add(promo); await db.commit(); await db.refresh(promo)
    return promo

@admin_router.patch("/promo-codes/{promo_id}")
async def admin_update_promo(promo_id: int, data: PromoCodeUpdate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    promo = await db.get(PromoCode, promo_id)
    if not promo:
        raise HTTPException(404, "Promo kod topilmadi")
    changes = data.model_dump(exclude_unset=True)
    if "code" in changes:
        changes["code"] = changes["code"].strip().upper()
        duplicate = (await db.execute(select(PromoCode.id).where(PromoCode.code == changes["code"], PromoCode.id != promo_id))).scalar_one_or_none()
        if duplicate:
            raise HTTPException(409, "Bu promo kod allaqachon mavjud")
    # Foiz va summa chegirmasi o'zaro istisno: biri berilsa ikkinchisi tozalanadi.
    if changes.get("discount_percent") is not None and "discount_amount" not in changes:
        changes["discount_amount"] = None
    if changes.get("discount_amount") is not None and "discount_percent" not in changes:
        changes["discount_percent"] = None
    final = {k: changes.get(k, getattr(promo, k)) for k in ("discount_percent", "discount_amount", "valid_from", "valid_until")}
    if final["discount_percent"] is None and final["discount_amount"] is None:
        raise HTTPException(422, "discount_percent yoki discount_amount dan biri kerak")
    if final["discount_percent"] is not None and final["discount_amount"] is not None:
        raise HTTPException(422, "discount_percent va discount_amount bir vaqtda berilmaydi")
    if final["valid_from"] and final["valid_until"] and final["valid_from"] > final["valid_until"]:
        raise HTTPException(422, "valid_until valid_from dan oldin bo'lishi mumkin emas")
    for key, value in changes.items():
        setattr(promo, key, value)
    await db.commit(); await db.refresh(promo)
    return promo


@admin_router.get("/products", response_model=list[ProductPublic])
async def admin_products(_: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(BillingProduct).order_by(BillingProduct.id))).scalars().all()

@admin_router.post("/products", status_code=201, response_model=ProductPublic)
async def create_product(data: ProductCreate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    if data.product_type not in (BillingProductType.FULL_EXAM, BillingProductType.WRITING_AI_CREDITS, BillingProductType.TEACHER_REVIEW_CREDITS):
        raise HTTPException(422, "Billing katalogida FULL_EXAM, WRITING_AI_CREDITS yoki TEACHER_REVIEW_CREDITS mahsuloti yaratilishi mumkin")
    exists = (await db.execute(select(BillingProduct.id).where(BillingProduct.code == data.code))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Bu mahsulot code'i allaqachon mavjud")
    if data.product_type == BillingProductType.FULL_EXAM:
        if not data.external_ref:
            raise HTTPException(422, "FULL_EXAM mahsuloti uchun external_ref = MockExam ID majburiy")
        from app.modules.exams.mock.models import MockExam
        exam = await db.get(MockExam, data.external_ref)
        if not exam or not exam.is_active:
            raise HTTPException(404, "Bog‘langan MockExam topilmadi")
    elif data.product_type == BillingProductType.WRITING_AI_CREDITS:
        try:
            credits = int(data.external_ref or "0")
        except ValueError:
            credits = 0
        if credits < 1 or credits > 10000:
            raise HTTPException(422, "Writing AI mahsuloti external_ref maydonida 1–10000 oralig‘idagi kredit soni bo‘lishi kerak")
    if data.product_type == BillingProductType.TEACHER_REVIEW_CREDITS:
        try:
            credits = int(data.external_ref or "0")
        except ValueError:
            credits = 0
        if credits < 1 or credits > 10000:
            raise HTTPException(422, "Teacher review mahsuloti external_ref maydonida 1–10000 oralig‘idagi tekshiruvlar soni bo‘lishi kerak")
    product = BillingProduct(**data.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@admin_router.patch("/products/{product_id}", response_model=ProductPublic)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(BillingProduct, product_id)
    if not product:
        raise HTTPException(404, "Billing mahsuloti topilmadi")
    changes = data.model_dump(exclude_unset=True)
    if "code" in changes:
        changes["code"] = changes["code"].strip().upper()
        duplicate = (await db.execute(select(BillingProduct.id).where(
            BillingProduct.code == changes["code"], BillingProduct.id != product_id,
        ))).scalar_one_or_none()
        if duplicate:
            raise HTTPException(409, "Bu mahsulot code'i allaqachon mavjud")
    external_ref = changes.get("external_ref", product.external_ref)
    is_active = changes.get("is_active", product.is_active)
    if is_active and product.product_type == BillingProductType.FULL_EXAM:
        if not external_ref:
            raise HTTPException(422, "FULL_EXAM mahsuloti uchun external_ref = MockExam ID majburiy")
        from app.modules.exams.mock.models import MockExam
        exam = await db.get(MockExam, external_ref)
        if not exam or not exam.is_active:
            raise HTTPException(404, "Bog‘langan faol MockExam topilmadi")
    elif is_active and product.product_type == BillingProductType.WRITING_AI_CREDITS:
        try:
            credits = int(external_ref or "0")
        except ValueError:
            credits = 0
        if credits < 1 or credits > 10000:
            raise HTTPException(422, "Writing AI mahsuloti external_ref maydonida 1–10000 oralig‘idagi kredit soni bo‘lishi kerak")
    elif is_active and product.product_type == BillingProductType.TEACHER_REVIEW_CREDITS:
        try:
            credits = int(external_ref or "0")
        except ValueError:
            credits = 0
        if credits < 1 or credits > 10000:
            raise HTTPException(422, "Teacher review mahsuloti external_ref maydonida 1–10000 oralig‘idagi tekshiruvlar soni bo‘lishi kerak")
    for key, value in changes.items():
        setattr(product, key, value)
    await db.commit()
    await db.refresh(product)
    return product


@router.post("/billing/telegram/review", dependencies=[Depends(verify_internal_token)])
async def telegram_review_order(data: dict, db: AsyncSession = Depends(get_db)):
    """Faqat botning internal tokeni bilan chaqiriladigan chek ko‘rib chiqish endpointi."""
    from uuid import UUID
    from app.core.config import settings
    from app.modules.auth.models import UserRole
    from app.modules.billing import review
    if not settings.TELEGRAM_BILLING_ADMIN_USER_ID:
        raise HTTPException(503, "TELEGRAM_BILLING_ADMIN_USER_ID sozlanmagan")
    try:
        reviewer_id = UUID(settings.TELEGRAM_BILLING_ADMIN_USER_ID)
        order_id = str(data.get("order_id", ""))
        action = str(data.get("action", ""))
    except (ValueError, TypeError):
        raise HTTPException(422, "Buyurtma yoki admin identifikatori noto‘g‘ri")
    admin = await db.get(User, reviewer_id)
    if not admin or admin.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        raise HTTPException(403, "Telegram billing admin foydalanuvchisi admin emas")
    order = await review.lock_order(db, order_id)
    if action == "approve":
        result = await review.approve(db, order, reviewer_id, "Telegram orqali tasdiqlandi")
    elif action == "reject":
        result = await review.reject(db, order, reviewer_id, "Telegram orqali rad etildi")
    else:
        raise HTTPException(422, "action approve yoki reject bo‘lishi kerak")
    return {"id": result.id, "status": result.status.value, "message": "Amal bajarildi"}


@admin_router.get("/reports/summary")
async def billing_report_summary(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Moliyaviy jamlanma: faqat tasdiqlangan tushum va alohida buyurtma holatlari."""
    from sqlalchemy import func, case
    filters = []
    if start_date:
        filters.append(BillingOrder.created_at >= start_date)
    if end_date:
        filters.append(BillingOrder.created_at < end_date)
    rows = (await db.execute(
        select(BillingOrder.status, func.count(BillingOrder.id),
               func.coalesce(func.sum(BillingOrder.amount), 0),
               func.coalesce(func.sum(BillingOrder.original_amount), 0),
               func.coalesce(func.sum(BillingOrder.discount_amount), 0))
        .where(*filters).group_by(BillingOrder.status)
    )).all()
    by_status = {}
    total_orders = paid_orders = revenue = original = discounts = 0
    for status, count, amount, original_amount, discount_amount in rows:
        by_status[status.value if hasattr(status, "value") else str(status)] = {"count": int(count), "amount": int(amount)}
        total_orders += int(count)
        if status == BillingOrderStatus.PAID:
            paid_orders = int(count)
            revenue = int(amount)
            original = int(original_amount)
            discounts = int(discount_amount)
    return {"total_orders": total_orders, "paid_orders": paid_orders, "confirmed_revenue": revenue,
            "paid_original_amount": original, "paid_discount_amount": discounts, "by_status": by_status,
            "currency": "UZS", "start_date": start_date, "end_date": end_date}


@admin_router.get("/orders", response_model=list[OrderPublic])
async def admin_orders(status: BillingOrderStatus | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    stmt = select(BillingOrder).order_by(BillingOrder.created_at.desc()).offset(offset).limit(limit)
    if status: stmt = stmt.where(BillingOrder.status == status)
    return (await db.execute(stmt)).scalars().all()

@admin_router.post("/orders/{order_id}/approve")
async def approve_order(order_id: str, body: ReviewBody, admin: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    from app.modules.billing import review
    order = await review.lock_order(db, order_id)
    return await review.approve(db, order, admin.id, body.note)

@admin_router.post("/orders/{order_id}/reject")
async def reject_order(order_id: str, body: ReviewBody, admin: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    from app.modules.billing import review
    order = await review.lock_order(db, order_id)
    return await review.reject(db, order, admin.id, body.note)

@admin_router.post("/users/{user_id}/subscription/cancel")
async def admin_cancel_user_subscription(
    user_id: UUID,
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Admin foydalanuvchining faol obunasini bekor qiladi."""
    now = datetime.now(timezone.utc)
    sub = (
        await db.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active.is_(True),
                UserSubscription.start_date <= now,
                UserSubscription.end_date > now,
            )
            .order_by(UserSubscription.end_date.desc())
            .limit(1)
        )
    ).scalars().first()
    if not sub:
        raise HTTPException(404, "Foydalanuvchida faol obuna topilmadi")
    sub.is_active = False
    await db.commit()
    return {"message": "Foydalanuvchi faol obunasi bekor qilindi", "is_active": False}


class PaymentDetails(BaseModel):
    # No invented bank/card credentials: until an administrator configures
    # verified requisites, the public API returns empty values and an explicit
    # unavailable message instead of plausible-looking placeholder details.
    card_number: str = Field(default="", max_length=50)
    card_holder: str = Field(default="", max_length=100)
    bank_name: str = Field(default="", max_length=100)
    account_number: Optional[str] = Field(default=None, max_length=50)
    phone_number: Optional[str] = Field(default=None, max_length=50)
    instructions: Optional[str] = Field(
        default="To'lov rekvizitlari hali sozlanmagan. Iltimos, keyinroq tekshiring.",
        max_length=400,
    )


@router.get("/billing/payment-details", response_model=PaymentDetails)
async def get_payment_details(db: AsyncSession = Depends(get_db)):
    """Ochiq endpoint: foydalanuvchi to'lov qilishi uchun bank va karta rekvizitlari."""
    from app.modules.admin.system_settings import SystemSetting
    row = (await db.execute(select(SystemSetting.value).where(SystemSetting.key == "billing_payment_details"))).scalar_one_or_none()
    if row:
        try:
            return PaymentDetails(**json.loads(row))
        except Exception:
            pass
    return PaymentDetails()


@admin_router.put("/payment-details", response_model=PaymentDetails)
async def set_payment_details(
    data: PaymentDetails,
    admin: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Admin: to'lov rekvizitlarini (karta raqami, bank, ko'rsatma) yangilash."""
    from app.modules.admin.system_settings import SystemSetting
    raw = json.dumps(data.model_dump())
    setting = (await db.execute(select(SystemSetting).where(SystemSetting.key == "billing_payment_details"))).scalar_one_or_none()
    if setting:
        setting.value = raw
        setting.updated_by = admin.id
    else:
        setting = SystemSetting(key="billing_payment_details", value=raw, updated_by=admin.id)
        db.add(setting)
    await db.commit()
    return data


