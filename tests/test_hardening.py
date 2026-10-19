"""Xavfsizlik tuzatishlari: billing (atomiklik, o'z buyurtmasi, bekor qilish, PATCH),
test kontentiga kirish nazorati va Exams loginini mustahkamlash (haqiqiy SQLite)."""
import asyncio
import itertools
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_hardening.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import ContactType, User, UserContact, UserProfile, UserRole
from app.modules.billing.models import (
    BillingOrder, BillingOrderStatus, BillingProduct, BillingProductType, PromoCode, PromoCodeUsage,
    SubscriptionPlan, SubscriptionTier, UserSubscription,
)
from app.modules.billing.router import admin_router as admin_billing_router, router as billing_router
from app.modules.exams.auth.router import router as exams_auth_router
from app.modules.tests import content_access
from app.modules.tests.listening.router import router as listening_router
from app.modules.exams.mock.models import (
    ExamSession, MockExam, MockPurchase, MockRegistration, RegistrationStatus,
)
from app.modules.tests.reading.models import ReadingTest
from app.modules.tests.listening.models import ListeningTest
from app.modules.tests.reading.router import router as reading_router

pytestmark = pytest.mark.asyncio
API = "/api/v1"
_mk_counter = itertools.count(1)


def _next_mk_id() -> str:
    n = next(_mk_counter)
    return f"MK-010101-{n % 1000:03d}" if n < 1000 else f"MK-02{n // 1000:02d}01-{n % 1000:03d}"


# --------------------------------------------------------------------------
# Fixture: oddiy token o'rniga sarlavhalar orqali "kim" aniqlanadi
# --------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.state.limiter = limiter
    for r in (billing_router, admin_billing_router, reading_router, listening_router, exams_auth_router):
        app.include_router(r, prefix=API)

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user

    # content_access get_current_user_optional / get_current_exams_session'ni to'g'ridan-to'g'ri
    # chaqiradi (Depends emas), shuning uchun modul darajasida almashtiriladi.
    async def _optional(request: Request, db):
        uid = request.headers.get("X-Test-User")
        return await db.get(User, uuid.UUID(uid)) if uid else None

    class _Sess:
        def __init__(self, user, registration_id):
            self.user, self.registration_id = user, registration_id

    async def _exams(request: Request, db):
        reg_id = request.headers.get("X-Test-Reg")
        if not reg_id:
            raise HTTPException(401, "no exams token")
        reg = await db.get(MockRegistration, int(reg_id))
        return _Sess(await db.get(User, reg.user_id), reg.id)

    mp = pytest.MonkeyPatch()
    mp.setattr(content_access, "get_current_user_optional", _optional)
    mp.setattr(content_access, "get_current_exams_session", _exams)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    mp.undo()
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


def H(uid=None, reg=None):
    h = {}
    if uid:
        h["X-Test-User"] = str(uid)
    if reg:
        h["X-Test-Reg"] = str(reg)
    return h


async def mk_user(role=UserRole.STUDENT, phone=None):
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True)
        s.add(u); await s.flush()
        s.add(UserProfile(user_id=u.id, full_name="Test User"))
        if phone:
            s.add(UserContact(user_id=u.id, contact_type=ContactType.PHONE, value=phone))
        await s.commit()
        return u.id


# ==========================================================================
# BILLING
# ==========================================================================
async def mk_plan(tier=SubscriptionTier.PRO, name=None):
    async with dbm.AsyncSessionLocal() as s:
        p = SubscriptionPlan(name=name or f"plan-{uuid.uuid4().hex[:6]}", price=50000, duration_days=30, tier=tier)
        s.add(p); await s.commit()
        return p.id


async def mk_sub_order(buyer, plan_id, status=BillingOrderStatus.UNDER_REVIEW, **kw):
    async with dbm.AsyncSessionLocal() as s:
        o = BillingOrder(user_id=buyer, product_type=BillingProductType.SUBSCRIPTION, product_id=str(plan_id),
                         product_title="Pro", amount=50000, original_amount=50000, status=status, **kw)
        s.add(o); await s.commit()
        return o.id


async def count_subs(user_id):
    async with dbm.AsyncSessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(UserSubscription)
                                .where(UserSubscription.user_id == user_id))).scalar_one()


async def test_concurrent_approve_grants_only_once(env):
    buyer = await mk_user()
    admin_a, admin_b = await mk_user(UserRole.ADMIN), await mk_user(UserRole.ADMIN)
    order_id = await mk_sub_order(buyer, await mk_plan())
    url = f"{API}/admin/billing/orders/{order_id}/approve"
    ra, rb = await asyncio.gather(
        env.post(url, json={}, headers=H(admin_a)), env.post(url, json={}, headers=H(admin_b)),
    )
    assert sorted([ra.status_code, rb.status_code]) == [200, 409]
    assert await count_subs(buyer) == 1


async def test_admin_cannot_approve_own_order(env):
    admin = await mk_user(UserRole.ADMIN)
    order_id = await mk_sub_order(admin, await mk_plan())
    r = await env.post(f"{API}/admin/billing/orders/{order_id}/approve", json={}, headers=H(admin))
    assert r.status_code == 403
    assert await count_subs(admin) == 0
    # buyurtma tekshiruvda qoladi — boshqa admin tasdiqlay oladi
    other = await mk_user(UserRole.ADMIN)
    assert (await env.post(f"{API}/admin/billing/orders/{order_id}/approve", json={}, headers=H(other))).status_code == 200


async def test_reject_after_approve_conflicts_and_does_not_flip_status(env):
    buyer, admin = await mk_user(), await mk_user(UserRole.ADMIN)
    order_id = await mk_sub_order(buyer, await mk_plan())
    assert (await env.post(f"{API}/admin/billing/orders/{order_id}/approve", json={}, headers=H(admin))).status_code == 200
    assert (await env.post(f"{API}/admin/billing/orders/{order_id}/reject", json={}, headers=H(admin))).status_code == 409
    async with dbm.AsyncSessionLocal() as s:
        assert (await s.get(BillingOrder, order_id)).status == BillingOrderStatus.PAID


async def test_approve_with_missing_plan_rolls_back_claim(env):
    buyer, admin = await mk_user(), await mk_user(UserRole.ADMIN)
    order_id = await mk_sub_order(buyer, 999_999)  # mavjud bo'lmagan tarif
    r = await env.post(f"{API}/admin/billing/orders/{order_id}/approve", json={}, headers=H(admin))
    assert r.status_code == 409
    async with dbm.AsyncSessionLocal() as s:
        # claim bekor qilingan: buyurtma hamon UNDER_REVIEW, qayta ko'rib chiqish mumkin
        assert (await s.get(BillingOrder, order_id)).status == BillingOrderStatus.UNDER_REVIEW


async def test_cancel_releases_promo_and_blocks_review_states(env):
    buyer = await mk_user()
    async with dbm.AsyncSessionLocal() as s:
        promo = PromoCode(code=f"P{uuid.uuid4().hex[:6].upper()}", discount_percent=10, max_usage=1, used_count=1)
        s.add(promo); await s.flush()
        s.add(PromoCodeUsage(promo_code_id=promo.id, user_id=buyer))
        await s.commit()
        promo_id = promo.id
    order_id = await mk_sub_order(buyer, await mk_plan(), status=BillingOrderStatus.PENDING_PAYMENT, promo_code_id=promo_id)

    r = await env.post(f"{API}/billing/orders/{order_id}/cancel", headers=H(buyer))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    async with dbm.AsyncSessionLocal() as s:
        assert (await s.get(PromoCode, promo_id)).used_count == 0
        usage = (await s.execute(select(PromoCodeUsage).where(PromoCodeUsage.promo_code_id == promo_id))).first()
        assert usage is None
    # ikkinchi marta bekor qilib bo'lmaydi
    assert (await env.post(f"{API}/billing/orders/{order_id}/cancel", headers=H(buyer))).status_code == 409

    under_review = await mk_sub_order(buyer, await mk_plan())
    assert (await env.post(f"{API}/billing/orders/{under_review}/cancel", headers=H(buyer))).status_code == 409
    # boshqa foydalanuvchi buyurtmasini bekor qila olmaydi
    stranger = await mk_user()
    pending = await mk_sub_order(buyer, await mk_plan(), status=BillingOrderStatus.PENDING_PAYMENT)
    assert (await env.post(f"{API}/billing/orders/{pending}/cancel", headers=H(stranger))).status_code == 404


async def test_plan_patch_changes_only_sent_fields(env):
    admin = await mk_user(UserRole.ADMIN)
    async with dbm.AsyncSessionLocal() as s:
        p = SubscriptionPlan(name=f"old-{uuid.uuid4().hex[:6]}", price=70000, duration_days=90,
                             tier=SubscriptionTier.PREMIUM, ai_limit=5, exam_limit=2, is_active=False)
        s.add(p); await s.commit()
        pid = p.id
    r = await env.patch(f"{API}/admin/billing/plans/{pid}", json={"price": 80000}, headers=H(admin))
    assert r.status_code == 200
    j = r.json()
    assert j["price"] == 80000
    # avval PlanCreate defaultlariga qaytib, o'chirilgan tarif qayta yoqilib qolardi
    assert j["is_active"] is False and j["duration_days"] == 90 and j["tier"] == "plus"
    assert j["ai_limit"] == 5 and j["exam_limit"] == 2
    assert (await env.patch(f"{API}/admin/billing/plans/{pid}", json={"tier": "free"}, headers=H(admin))).status_code == 422
    assert (await env.patch(f"{API}/admin/billing/plans/{pid}", json={"price": None}, headers=H(admin))).status_code == 422


async def test_promo_patch_keeps_limits_and_switches_discount_type(env):
    admin = await mk_user(UserRole.ADMIN)
    code = f"K{uuid.uuid4().hex[:6].upper()}"
    r = await env.post(f"{API}/admin/billing/promo-codes", headers=H(admin),
                       json={"code": code, "discount_percent": 20, "max_usage": 5})
    assert r.status_code == 201
    pid = r.json()["id"]
    # faqat is_active o'zgaradi: max_usage None ga tushib ketmaydi
    r = await env.patch(f"{API}/admin/billing/promo-codes/{pid}", json={"is_active": False}, headers=H(admin))
    assert r.status_code == 200 and r.json()["max_usage"] == 5 and r.json()["discount_percent"] == 20
    # summa chegirmasiga o'tish foizni tozalaydi
    r = await env.patch(f"{API}/admin/billing/promo-codes/{pid}", json={"discount_amount": 15000}, headers=H(admin))
    assert r.status_code == 200 and r.json()["discount_amount"] == 15000 and r.json()["discount_percent"] is None
    # aniq null bilan limitni olib tashlash mumkin
    r = await env.patch(f"{API}/admin/billing/promo-codes/{pid}", json={"max_usage": None}, headers=H(admin))
    assert r.status_code == 200 and r.json()["max_usage"] is None
    # ikkala chegirmani bir vaqtda berib bo'lmaydi
    r = await env.patch(f"{API}/admin/billing/promo-codes/{pid}",
                        json={"discount_percent": 10, "discount_amount": 5000}, headers=H(admin))
    assert r.status_code == 422


# ==========================================================================
# TEST KONTENTIGA KIRISH
# ==========================================================================
async def mk_tests(**flags):
    """Reading + Listening test (bir xil bayroqlar bilan) yaratadi."""
    ids = {}
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))
        for key, model in (("reading", ReadingTest), ("listening", ListeningTest)):
            tid = f"{key[0]}-{uuid.uuid4().hex[:8]}"
            kwargs = dict(id=tid, title=f"{key} test", cefr_level="B2", **flags)
            if key == "reading":
                kwargs["total_questions"] = 35
            else:
                kwargs["sections"] = "[]"
            s.add(model(**kwargs))
            ids[key] = tid
        await s.commit()
    return ids


async def test_anonymous_access_by_catalog_rules(env):
    free = await mk_tests(is_practice=True, is_free=True, is_active=True)
    paid = await mk_tests(is_practice=True, is_free=False, is_active=True)
    mock = await mk_tests(is_practice=False, is_free=False, is_active=True)
    off = await mk_tests(is_practice=True, is_free=True, is_active=False)

    assert (await env.get(f"{API}/reading-tests/{free['reading']}")).status_code == 200
    assert (await env.get(f"{API}/listening-tests/{free['listening']}")).status_code == 200
    for ids in (paid, mock):
        assert (await env.get(f"{API}/reading-tests/{ids['reading']}")).status_code == 401
        assert (await env.get(f"{API}/listening-tests/{ids['listening']}")).status_code == 401
    assert (await env.get(f"{API}/reading-tests/{off['reading']}")).status_code == 404

    listed = {t["id"] for t in (await env.get(f"{API}/reading-tests")).json()}
    assert free["reading"] in listed
    assert not ({paid["reading"], mock["reading"], off["reading"]} & listed)
    listed_l = {t["id"] for t in (await env.get(f"{API}/listening-tests")).json()}
    assert free["listening"] in listed_l and paid["listening"] not in listed_l


async def test_free_vs_pro_vs_admin_on_paid_practice(env):
    paid = await mk_tests(is_practice=True, is_free=False, is_active=True)
    free_user, pro_user, admin = await mk_user(), await mk_user(), await mk_user(UserRole.ADMIN)
    plan = await mk_plan(SubscriptionTier.PRO)
    async with dbm.AsyncSessionLocal() as s:
        now = datetime.now(timezone.utc)
        s.add(UserSubscription(user_id=pro_user, plan_id=plan, start_date=now - timedelta(days=1),
                               end_date=now + timedelta(days=30), is_active=True))
        await s.commit()

    url = f"{API}/reading-tests/{paid['reading']}"
    assert (await env.get(url, headers=H(free_user))).status_code == 403
    assert (await env.get(url, headers=H(pro_user))).status_code == 200
    assert (await env.get(url, headers=H(admin))).status_code == 200

    ids_free = {t["id"] for t in (await env.get(f"{API}/reading-tests", headers=H(free_user))).json()}
    ids_pro = {t["id"] for t in (await env.get(f"{API}/reading-tests", headers=H(pro_user))).json()}
    assert paid["reading"] not in ids_free and paid["reading"] in ids_pro


async def _mock_exam(tests, price=0):
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))
        e = MockExam(id=f"mx-{uuid.uuid4().hex[:8]}", title="Mock",
                     reading_id=tests["reading"], listening_id=tests["listening"], speaking_id="sp", price=price)
        s.add(e); await s.flush()
        sess = ExamSession(mock_exam_id=e.id, exam_date=datetime.now(timezone.utc) + timedelta(hours=1),
                           location_address="Tashkent", registration_slug=f"slug{uuid.uuid4().hex[:10]}")
        s.add(sess); await s.commit()
        return e.id, sess.id


async def _registration(user_id, exam_id, session_id, status=RegistrationStatus.REGISTERED):
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))
        r = MockRegistration(
            user_id=user_id, session_id=session_id, mock_exam_id=exam_id, full_name="Ali Valiyev",
            student_id=_next_mk_id(), exam_access_id=f"EXAM-{uuid.uuid4().hex}",
            qr_token=uuid.uuid4().hex, age=18, gender="male", region="Toshkent", photo_url="x", status=status,
        )
        s.add(r); await s.commit()
        return r.id, r.student_id, r.exam_access_id


async def test_admin_can_update_and_deactivate_billing_product(env):
    admin = await mk_user(UserRole.ADMIN)
    code = f"AI-{uuid.uuid4().hex[:8].upper()}"
    async with dbm.AsyncSessionLocal() as s:
        product = BillingProduct(
            product_type=BillingProductType.WRITING_AI_CREDITS,
            code=code, title="Writing AI credits", price=15000,
            external_ref="10", is_active=True,
        )
        s.add(product)
        await s.commit()
        product_id = product.id

    updated = await env.patch(
        f"{API}/admin/billing/products/{product_id}",
        json={"price": 20000, "is_active": False},
        headers=H(admin),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["price"] == 20000
    assert updated.json()["is_active"] is False
    public = await env.get(f"{API}/billing/products?product_type=writing_ai_credits")
    assert public.status_code == 200
    assert all(row["id"] != product_id for row in public.json())


async def test_paid_mock_exam_order_uses_exam_id_and_is_idempotent(env):
    tests = await mk_tests(is_practice=False, is_free=False, is_active=True)
    exam_id, _ = await _mock_exam(tests, price=25000)
    buyer = await mk_user()
    payload = {"product_type": "mock_exam", "product_id": exam_id}

    first = await env.post(f"{API}/billing/orders", json=payload, headers=H(buyer))
    assert first.status_code == 201, first.text
    first_data = first.json()
    assert first_data["product_id"] == exam_id
    assert first_data["product_title"] == "Mock"
    assert first_data["amount"] == 25000
    assert first_data["status"] == BillingOrderStatus.PENDING_PAYMENT.value
    # Takroriy click yangi buyurtma yaratmaydi.
    second = await env.post(f"{API}/billing/orders", json=payload, headers=H(buyer))
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first_data["id"]
    async with dbm.AsyncSessionLocal() as s:
        count = await s.scalar(select(func.count()).select_from(BillingOrder).where(
            BillingOrder.user_id == buyer,
            BillingOrder.product_type == BillingProductType.MOCK_EXAM,
            BillingOrder.product_id == exam_id,
        ))
    assert count == 1


async def test_mock_test_visible_only_to_enrolled_candidates(env):
    mock = await mk_tests(is_practice=False, is_free=False, is_active=True)
    other = await mk_tests(is_practice=False, is_free=False, is_active=True)
    exam_id, session_id = await _mock_exam(mock)
    other_exam, other_session = await _mock_exam(other)

    candidate = await mk_user()
    reg_id, _, _ = await _registration(candidate, exam_id, session_id)
    other_reg, _, _ = await _registration(await mk_user(), other_exam, other_session)
    cancelled_reg, _, _ = await _registration(await mk_user(), exam_id, session_id, RegistrationStatus.CANCELLED)

    url_r, url_l = f"{API}/reading-tests/{mock['reading']}", f"{API}/listening-tests/{mock['listening']}"
    # Exams sessiyasi: o'z imtihoni — ha; boshqa imtihon / bekor qilingan registratsiya — yo'q
    assert (await env.get(url_r, headers=H(reg=reg_id))).status_code == 200
    assert (await env.get(url_l, headers=H(reg=reg_id))).status_code == 200
    assert (await env.get(url_r, headers=H(reg=other_reg))).status_code == 404
    assert (await env.get(url_r, headers=H(reg=cancelled_reg))).status_code == 404
    # Ro'yxatda faqat o'z testi
    own = [t["id"] for t in (await env.get(f"{API}/reading-tests", headers=H(reg=reg_id))).json()]
    assert own == [mock["reading"]]

    # Oddiy foydalanuvchi: yozilmagan -> 404; MockPurchase bilan -> 200
    buyer = await mk_user()
    assert (await env.get(url_r, headers=H(buyer))).status_code == 404
    async with dbm.AsyncSessionLocal() as s:
        s.add(MockPurchase(user_id=buyer, mock_exam_id=exam_id, is_active=True))
        await s.commit()
    assert (await env.get(url_r, headers=H(buyer))).status_code == 200


# ==========================================================================
# EXAMS LOGIN
# ==========================================================================
async def _login_fixture(phone=None, status=RegistrationStatus.REGISTERED, minutes_to_start=60):
    phone = phone or f"+99890{uuid.uuid4().int % 10_000_000:07d}"
    tests = await mk_tests(is_practice=False, is_active=True)
    exam_id, session_id = await _mock_exam(tests)
    async with dbm.AsyncSessionLocal() as s:
        sess = await s.get(ExamSession, session_id)
        sess.exam_date = datetime.now(timezone.utc) + timedelta(minutes=minutes_to_start)
        await s.commit()
    user = await mk_user(phone=phone)
    return await _registration(user, exam_id, session_id, status)


async def test_login_by_mk_id_can_be_disabled(env, monkeypatch):
    _, mk_id, exam_access = await _login_fixture()
    monkeypatch.setattr(settings, "EXAMS_MK_ID_LOGIN_ENABLED", False)
    r = await env.post(f"{API}/exams-auth/login", json={"student_id": mk_id})
    assert r.status_code == 404
    # tasodifiy exam_id bilan kirish ishlashda davom etadi
    r = await env.post(f"{API}/exams-auth/login", json={"exam_id": exam_access})
    assert r.status_code == 200 and r.json()["status"] == "success"


async def test_second_factor_required_and_throttled(env, monkeypatch):
    _, mk_id, _ = await _login_fixture(phone="+998 90 123 45 67")
    monkeypatch.setattr(settings, "EXAMS_MK_ID_REQUIRE_PHONE_LAST4", True)
    url = f"{API}/exams-auth/login"

    assert (await env.post(url, json={"student_id": mk_id})).status_code == 404            # omil yo'q
    assert (await env.post(url, json={"student_id": mk_id, "phone_last4": "0000"})).status_code == 404
    assert (await env.post(url, json={"student_id": "MK-000000-000", "phone_last4": "4567"})).status_code == 404
    ok = await env.post(url, json={"student_id": mk_id, "phone_last4": "4567"})
    assert ok.status_code == 200 and ok.json()["token"]["access_token"]
    assert ok.json()["student"].get("phone_last4") is None  # omil javobda qaytib chiqmaydi

    # 5 ta noto'g'ri urinishdan keyin to'g'ri omil ham vaqtincha bloklanadi
    _, mk2, _ = await _login_fixture(phone="+998909998877")
    for _ in range(5):
        assert (await env.post(url, json={"student_id": mk2, "phone_last4": "1111"})).status_code == 404
    assert (await env.post(url, json={"student_id": mk2, "phone_last4": "8877"})).status_code == 429


async def test_phone_last4_format_is_validated(env):
    r = await env.post(f"{API}/exams-auth/login", json={"student_id": "MK-010101-001", "phone_last4": "12ab"})
    assert r.status_code == 422


async def test_login_window(env, monkeypatch):
    _, _, soon = await _login_fixture(minutes_to_start=20)
    _, _, later = await _login_fixture(minutes_to_start=600)
    _, _, past = await _login_fixture(minutes_to_start=-600)
    monkeypatch.setattr(settings, "EXAMS_LOGIN_OPENS_MINUTES_BEFORE", 30)
    monkeypatch.setattr(settings, "EXAMS_LOGIN_CLOSES_MINUTES_AFTER", 240)
    url = f"{API}/exams-auth/login"
    assert (await env.post(url, json={"exam_id": soon})).status_code == 200
    assert (await env.post(url, json={"exam_id": later})).status_code == 403
    assert (await env.post(url, json={"exam_id": past})).status_code == 403
    # oyna sozlanmasa (None) — oldingi xulq saqlanadi
    monkeypatch.setattr(settings, "EXAMS_LOGIN_OPENS_MINUTES_BEFORE", None)
    monkeypatch.setattr(settings, "EXAMS_LOGIN_CLOSES_MINUTES_AFTER", None)
    assert (await env.post(url, json={"exam_id": later})).status_code == 200
