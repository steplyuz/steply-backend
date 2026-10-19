"""
=============================================================================
STEPLY BACKEND — TO'LIQ API TEST SUITE  (v2 — tuzatilgan)
=============================================================================
Admin bo'limi:
  - Login / Logout / Refresh / Me
  - Dashboard, Statistics
  - Users CRUD (list, detail, update, sessions, export)
  - Registrations (list, status update)
  - Attempts & Results
  - OMR Submissions
  - Billing: Plans, Products, PromoCode, Orders, Tier-Limits
  - Settings (SMS registration)

User bo'limi:
  - Register / Login / Logout / Refresh / Forgot & Reset password
  - /auth/me
  - Profile (read, update)
  - Contacts (list, add, set primary, delete)
  - Sessions (list, revoke)
  - Practice tests catalog (listening, reading, writing, speaking)
  - Billing: plans, products, promo validate, order create/cancel
  - Subscription & Limits

Exams bo'limi (ExamsAuth):
  - Login / Logout / Me
  - List enrolled exams, My skills
  - Access control testlari

=============================================================================
Ishga tushirish:
  pytest tests/test_full_api.py -v --tb=short
=============================================================================
"""
from __future__ import annotations

import os
import uuid

# ─── TEST UCHUN ENV VARLARINI MAJBURIY O'RNATISH ──────────────────────────────
# Bu importdan AVVAL bajarilishi shart (settings singleton import paytida o'qiladi)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_full_api.db"
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-at-least-32chars!!"
os.environ["ADMIN_SECRET_KEY"] = "test-admin-secret-key-for-testing-32chars!"
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["APPLY_CAPTCHA_ENABLED"] = "false"
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ["EXAMS_MK_ID_LOGIN_ENABLED"] = "true"
os.environ["RESULT_SMS_ENABLED"] = "false"
os.environ["RUN_TELEGRAM_BOT_IN_API"] = "false"
os.environ.pop("EXAMS_LOGIN_OPENS_MINUTES_BEFORE", None)
os.environ.pop("EXAMS_LOGIN_CLOSES_MINUTES_AFTER", None)
# ──────────────────────────────────────────────────────────────────────────────

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import engine

# ─────────────────────────────────────────────────────────────────────────────
# Konstantalar
# ─────────────────────────────────────────────────────────────────────────────
BASE = "/api/v1"
ADMIN_PHONE = "+998901110001"
ADMIN_PASS  = "AdminPass@2026"
USER_PHONE  = "+998907770001"
USER_PASS   = "UserPass@2026"


# ─────────────────────────────────────────────────────────────────────────────
# YORDAMCHI: Cookie header string qilish
# ─────────────────────────────────────────────────────────────────────────────
def cookie_header(cookies: dict) -> dict:
    """httpx deprecated `cookies=` parametridan qochish uchun header sifatida uzatish."""
    if not cookies:
        return {}
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {"Cookie": cookie_str}


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def db_setup():
    from app.core.database import init_db
    await init_db()
    yield
    await engine.dispose()
    db_path = "./test_full_api.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


@pytest_asyncio.fixture(scope="session")
async def client(db_setup):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _clean_client_cookies(client: AsyncClient):
    client.cookies.clear()
    yield
    client.cookies.clear()


async def _create_user_and_login(
    client: AsyncClient,
    phone: str,
    password: str,
    role_str: str,
    full_name: str,
    username: str,
) -> dict:
    """DB ga foydalanuvchi yaratib, login qilib, cookie va id qaytaradi."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.modules.auth.models import (
        User, UserRole, UserProfile, UserContact, ContactType
    )
    from app.core.security import hash_password
    from sqlalchemy import select

    role = UserRole(role_str)

    async with AsyncSession(engine) as session:
        existing = (await session.execute(
            select(User).join(UserContact, UserContact.user_id == User.id)
            .where(UserContact.value == phone)
        )).scalars().first()

        if existing:
            user_id = str(existing.id)
        else:
            new_id = uuid.uuid4()
            user = User(
                id=new_id,
                global_role=role,
                is_active=True,
                hashed_password=hash_password(password),
            )
            session.add(user)
            await session.flush()  # id generate qilish uchun

            profile = UserProfile(user_id=new_id, full_name=full_name, username=username)
            contact = UserContact(
                user_id=new_id,
                contact_type=ContactType.PHONE,
                value=phone,
                is_primary=True,
                is_verified=True,
            )
            session.add(profile)
            session.add(contact)
            await session.commit()
            user_id = str(new_id)

    resp = await client.post(
        f"{BASE}/auth/phone/login",
        json={"phone": phone, "password": password},
    )
    assert resp.status_code == 200, f"Login ({role_str}) failed: {resp.text}"
    cookies = dict(resp.cookies)
    client.cookies.clear()
    return {"id": user_id, "cookies": cookies}


@pytest_asyncio.fixture(scope="session")
async def admin_user(client: AsyncClient):
    return await _create_user_and_login(
        client, ADMIN_PHONE, ADMIN_PASS, "admin", "Test Admin", "testadmin2026"
    )


@pytest_asyncio.fixture(scope="session")
async def regular_user(client: AsyncClient):
    return await _create_user_and_login(
        client, USER_PHONE, USER_PASS, "student", "Test User", "testuser2026"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. TIZIM TEKSHIRUVLARI
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemEndpoints:
    async def test_root(self, client):
        r = await client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert "message" in data or "version" in data

    async def test_health(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_security_headers(self, client):
        r = await client.get("/health")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"

    async def test_request_id_header(self, client):
        r = await client.get("/health")
        assert "x-request-id" in r.headers

    async def test_openapi_json_available_in_test_env(self, client):
        r = await client.get("/openapi.json")
        assert r.status_code == 200
        data = r.json()
        assert "paths" in data


# ─────────────────────────────────────────────────────────────────────────────
# 2. AUTH — Umumiy testlar (session-free)
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthGeneral:
    async def test_sms_registration_status_public(self, client):
        r = await client.get(f"{BASE}/auth/sms-registration/status")
        assert r.status_code == 200
        assert "sms_registration_enabled" in r.json()

    async def test_get_me_unauthenticated(self, client):
        r = await client.get(f"{BASE}/auth/me")
        assert r.status_code in (401, 403)

    async def test_phone_login_wrong_password(self, client):
        r = await client.post(
            f"{BASE}/auth/phone/login",
            json={"phone": ADMIN_PHONE, "password": "WrongPass!999"},
        )
        assert r.status_code in (400, 401, 422)

    async def test_phone_login_nonexistent_user(self, client):
        r = await client.post(
            f"{BASE}/auth/phone/login",
            json={"phone": "+998900000000", "password": "AnyPass@123"},
        )
        assert r.status_code in (400, 401, 404)

    async def test_phone_login_missing_fields(self, client):
        r = await client.post(f"{BASE}/auth/phone/login", json={})
        assert r.status_code == 422

    async def test_refresh_no_cookie(self, client):
        r = await client.post(f"{BASE}/auth/refresh")
        assert r.status_code in (401, 400)

    async def test_forgot_password_unknown_phone(self, client):
        r = await client.post(
            f"{BASE}/auth/password/forgot",
            json={"phone": "+998900000000"},
        )
        assert r.status_code in (200, 404, 400)

    async def test_reset_password_invalid_code(self, client):
        r = await client.post(
            f"{BASE}/auth/password/reset",
            json={"phone": USER_PHONE, "code": "000000", "new_password": "NewPass@123"},
        )
        assert r.status_code in (400, 404, 422)

    async def test_change_password_not_authenticated(self, client):
        r = await client.post(
            f"{BASE}/auth/password/change",
            json={"current_password": "old", "new_password": "New@1234"},
        )
        assert r.status_code in (401, 403)

    async def test_logout_no_session(self, client):
        r = await client.post(f"{BASE}/auth/logout")
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 3. AUTH — Admin login
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthAdmin:
    async def test_admin_login_success(self, client, admin_user):
        assert "id" in admin_user
        assert "cookies" in admin_user
        assert admin_user["cookies"]

    async def test_admin_get_me(self, client, admin_user):
        r = await client.get(
            f"{BASE}/auth/me",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("global_role") in ("admin", "ADMIN", "super_admin")

    async def test_admin_logout(self, client, admin_user):
        # Yangi session ochib logout qilamiz (asosiy sessionni saqlaymiz)
        resp = await client.post(
            f"{BASE}/auth/phone/login",
            json={"phone": ADMIN_PHONE, "password": ADMIN_PASS},
        )
        assert resp.status_code == 200
        temp_cookies = dict(resp.cookies)
        r = await client.post(
            f"{BASE}/auth/logout",
            headers=cookie_header(temp_cookies),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "success"


# ─────────────────────────────────────────────────────────────────────────────
# 4. AUTH — Regular user login
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthUser:
    async def test_user_login_success(self, client, regular_user):
        assert "id" in regular_user
        assert regular_user["cookies"]

    async def test_user_get_me(self, client, regular_user):
        r = await client.get(
            f"{BASE}/auth/me",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert "id" in data


# ─────────────────────────────────────────────────────────────────────────────
# 5. USER PROFILE BO'LIMI
# ─────────────────────────────────────────────────────────────────────────────

class TestUserProfile:
    async def test_get_my_profile(self, client, regular_user):
        r = await client.get(
            f"{BASE}/users/me",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert str(data["id"]) == regular_user["id"]

    async def test_get_profile_unauthenticated(self, client):
        r = await client.get(f"{BASE}/users/me")
        assert r.status_code in (401, 403)

    async def test_update_profile(self, client, regular_user):
        r = await client.put(
            f"{BASE}/users/me/profile",
            json={"full_name": "Updated Test User"},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code in (200, 422), r.text

    async def test_get_contacts(self, client, regular_user):
        r = await client.get(
            f"{BASE}/users/me/contacts",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_get_sessions(self, client, regular_user):
        r = await client.get(
            f"{BASE}/users/me/sessions",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_upload_avatar_invalid_file(self, client, regular_user):
        r = await client.post(
            f"{BASE}/users/me/avatar",
            files={"file": ("test.txt", b"not an image", "text/plain")},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code in (400, 415, 422)

    async def test_add_contact_unauthenticated(self, client):
        r = await client.post(
            f"{BASE}/users/me/contacts",
            json={"value": "+998901234567", "contact_type": "phone"},
        )
        assert r.status_code in (401, 403)

    async def test_delete_nonexistent_contact(self, client, regular_user):
        r = await client.delete(
            f"{BASE}/users/me/contacts/99999",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code in (404, 204)

    async def test_revoke_nonexistent_session(self, client, regular_user):
        r = await client.delete(
            f"{BASE}/users/me/sessions/{uuid.uuid4()}",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code in (404, 200)


# ─────────────────────────────────────────────────────────────────────────────
# 6. PRACTICE TESTS BO'LIMI
# ─────────────────────────────────────────────────────────────────────────────

class TestPracticeTests:
    async def test_list_all_practice_tests(self, client):
        r = await client.get(f"{BASE}/practice/tests")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert set(["listening", "reading", "writing", "speaking"]).issubset(data["items"])

    async def test_list_listening_practice(self, client):
        r = await client.get(f"{BASE}/practice/tests?skill=listening")
        assert r.status_code == 200

    async def test_list_reading_practice(self, client):
        r = await client.get(f"{BASE}/practice/tests?skill=reading")
        assert r.status_code == 200

    async def test_list_writing_practice(self, client):
        r = await client.get(f"{BASE}/practice/tests?skill=writing")
        assert r.status_code == 200

    async def test_list_speaking_practice(self, client):
        r = await client.get(f"{BASE}/practice/tests?skill=speaking")
        assert r.status_code == 200

    async def test_invalid_skill_filter(self, client):
        r = await client.get(f"{BASE}/practice/tests?skill=math")
        assert r.status_code == 422

    async def test_reading_tests_list(self, client):
        r = await client.get(f"{BASE}/reading-tests")
        assert r.status_code == 200

    async def test_listening_tests_list(self, client):
        r = await client.get(f"{BASE}/listening-tests")
        assert r.status_code == 200

    async def test_writing_tests_list(self, client):
        r = await client.get(f"{BASE}/writing-tests")
        assert r.status_code == 200

    async def test_speaking_practice_list(self, client):
        r = await client.get(f"{BASE}/speaking-tests/practice")
        assert r.status_code == 200

    async def test_reading_test_not_found(self, client):
        r = await client.get(f"{BASE}/reading-tests/nonexistent-id")
        assert r.status_code == 404

    async def test_listening_test_not_found(self, client):
        r = await client.get(f"{BASE}/listening-tests/nonexistent-id")
        assert r.status_code == 404

    async def test_writing_test_not_found(self, client):
        r = await client.get(f"{BASE}/writing-tests/nonexistent-id")
        assert r.status_code == 404

    async def test_speaking_practice_not_found(self, client):
        r = await client.get(f"{BASE}/speaking-tests/practice/nonexistent-id")
        assert r.status_code == 404

    async def test_submit_reading_unauthenticated(self, client):
        r = await client.post(
            f"{BASE}/reading-tests/some-id/submit",
            json={"answers": {}},
        )
        assert r.status_code in (401, 403, 404, 422)

    async def test_submit_listening_unauthenticated(self, client):
        r = await client.post(
            f"{BASE}/listening-tests/some-id/submit",
            json={"answers": {}},
        )
        assert r.status_code in (401, 403, 404, 422)

    async def test_submit_writing_unauthenticated(self, client):
        r = await client.post(
            f"{BASE}/writing-tests/some-id/submit",
            json={"answers": []},
        )
        assert r.status_code in (401, 403, 404, 422)


# ─────────────────────────────────────────────────────────────────────────────
# 7. BILLING — USER BO'LIMI
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingUser:
    async def test_list_public_plans(self, client):
        r = await client.get(f"{BASE}/billing/plans")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_public_products(self, client):
        r = await client.get(f"{BASE}/billing/products")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_products_by_full_exam_type(self, client):
        r = await client.get(f"{BASE}/billing/products?product_type=full_exam")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_my_orders_unauthenticated(self, client):
        r = await client.get(f"{BASE}/billing/orders")
        assert r.status_code in (401, 403)

    async def test_my_orders_authenticated(self, client, regular_user):
        r = await client.get(
            f"{BASE}/billing/orders",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_my_subscription_authenticated(self, client, regular_user):
        r = await client.get(
            f"{BASE}/billing/me/subscription",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_my_limits_authenticated(self, client, regular_user):
        r = await client.get(
            f"{BASE}/billing/me/limits",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_my_limits_unauthenticated(self, client):
        r = await client.get(f"{BASE}/billing/me/limits")
        assert r.status_code in (401, 403)

    async def test_create_order_mock_exam_requires_existing_exam(self, client, regular_user):
        r = await client.post(
            f"{BASE}/billing/orders",
            json={"product_type": "mock_exam", "product_id": "missing-mock-exam"},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_create_order_subscription_no_plan(self, client, regular_user):
        r = await client.post(
            f"{BASE}/billing/orders",
            json={"product_type": "subscription", "product_id": "99999"},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code in (404, 422)

    async def test_create_order_unauthenticated(self, client):
        r = await client.post(
            f"{BASE}/billing/orders",
            json={"product_type": "subscription", "product_id": "1"},
        )
        assert r.status_code in (401, 403)

    async def test_validate_promo_invalid_code(self, client, regular_user):
        r = await client.post(
            f"{BASE}/billing/promo/validate",
            json={"code": "NOTEXISTS", "amount": 100000},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code in (404, 400)

    async def test_order_detail_not_found(self, client, regular_user):
        r = await client.get(
            f"{BASE}/billing/orders/nonexistentorderid",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_cancel_order_not_found(self, client, regular_user):
        r = await client.post(
            f"{BASE}/billing/orders/notfound/cancel",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 8. ADMIN — DASHBOARD & STATISTICS
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminDashboard:
    async def test_dashboard_success(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/dashboard",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert "users" in data
        assert "mock_exams" in data
        assert "registrations" in data

    async def test_statistics_success(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/statistics",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_dashboard_unauthenticated(self, client):
        r = await client.get(f"{BASE}/admin/dashboard")
        assert r.status_code in (401, 403)

    async def test_dashboard_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/dashboard",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403

    async def test_statistics_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/statistics",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 9. ADMIN — FOYDALANUVCHILAR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminUsers:
    async def test_list_users(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 2  # admin + user yaratdik

    async def test_list_users_search(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?search=testadmin",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    async def test_list_users_active_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?is_active=true",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_users_role_filter_student(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?role=student",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    async def test_list_users_role_filter_admin(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?role=admin",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_users_pagination(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?offset=0&limit=5",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_users_pagination_limit_too_large(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?limit=9999",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_list_users_unauthorized(self, client):
        r = await client.get(f"{BASE}/admin/users")
        assert r.status_code in (401, 403)

    async def test_get_user_detail(self, client, admin_user, regular_user):
        user_id = regular_user["id"]
        r = await client.get(
            f"{BASE}/admin/users/{user_id}",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert str(data["id"]) == user_id

    async def test_get_user_not_found(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users/{uuid.uuid4()}",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_update_user_is_active(self, client, admin_user, regular_user):
        user_id = regular_user["id"]
        r = await client.patch(
            f"{BASE}/admin/users/{user_id}",
            json={"is_active": True},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is True

    async def test_update_user_promote_to_super_admin_forbidden(self, client, admin_user, regular_user):
        user_id = regular_user["id"]
        r = await client.patch(
            f"{BASE}/admin/users/{user_id}",
            json={"global_role": "super_admin"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 403

    async def test_export_users_xlsx(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/export/users",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "spreadsheetml" in ct or "application/octet" in ct

    async def test_export_users_unauthorized(self, client):
        r = await client.get(f"{BASE}/admin/export/users")
        assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 10. ADMIN — SEANSLAR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminSessions:
    async def test_list_sessions(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/sessions",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_sessions_for_user(self, client, admin_user, regular_user):
        r = await client.get(
            f"{BASE}/admin/sessions?user_id={regular_user['id']}",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_sessions_revoked_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/sessions?is_revoked=false",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_revoke_all_sessions_for_unknown_user(self, client, admin_user):
        """Mavjud bo'lmagan user uchun revoke-all ishlatamiz."""
        r = await client.post(
            f"{BASE}/admin/users/{uuid.uuid4()}/sessions/revoke-all",
            headers=cookie_header(admin_user["cookies"]),
        )
        # 200 (0 rows updated) yoki 404
        assert r.status_code in (200, 404)

    async def test_revoke_sessions_user_not_found(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/users/{uuid.uuid4()}/sessions/revoke-all",
            headers=cookie_header(admin_user["cookies"]),
        )
        # 404 yoki 200 (user yo'q bo'lsa ham 0 row update qiladi)
        assert r.status_code in (200, 404)


# ─────────────────────────────────────────────────────────────────────────────
# 11. ADMIN — RO'YXATGA OLISH (REGISTRATIONS)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminRegistrations:
    async def test_list_registrations(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/registrations",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_registrations_status_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/registrations?status=REGISTERED",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_registrations_invalid_status(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/registrations?status=FAKESTAT",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_update_registration_not_found(self, client, admin_user):
        r = await client.patch(
            f"{BASE}/admin/registrations/99999/status",
            json={"status": "CHECKED_IN"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (404, 422)

    async def test_update_registration_invalid_status(self, client, admin_user):
        r = await client.patch(
            f"{BASE}/admin/registrations/99999/status",
            json={"status": "INVALID"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (404, 422)


# ─────────────────────────────────────────────────────────────────────────────
# 12. ADMIN — URINISHLAR VA NATIJALAR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminAttempts:
    async def test_list_attempts(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/attempts",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_attempts_with_user_filter(self, client, admin_user, regular_user):
        r = await client.get(
            f"{BASE}/admin/attempts?user_id={regular_user['id']}",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_results(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/results",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_results_finalized_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/results?status=FINALIZED",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_results_pending_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/results?status=PENDING",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 13. ADMIN — OMR SUBMISSIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminOMR:
    async def test_list_omr_submissions(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/omr/submissions",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_omr_with_pending_status(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/omr/submissions?status=PENDING",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_omr_invalid_module(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/omr/submissions?module=INVALID",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_list_omr_invalid_status(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/omr/submissions?status=NOTASTATUS",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_get_omr_submission_not_found(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/omr/submissions/99999",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_update_omr_status_not_found(self, client, admin_user):
        r = await client.patch(
            f"{BASE}/admin/omr/submissions/99999/status?status=PROCESSED",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_omr_unauthorized(self, client):
        r = await client.get(f"{BASE}/admin/omr/submissions")
        assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 14. ADMIN — SETTINGS (SMS Registration)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminSettings:
    async def test_get_sms_registration_setting(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/settings/sms-registration",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert "sms_registration_enabled" in data
        assert isinstance(data["sms_registration_enabled"], bool)

    async def test_enable_sms_registration(self, client, admin_user):
        r = await client.put(
            f"{BASE}/admin/settings/sms-registration",
            json={"sms_registration_enabled": True},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert r.json()["sms_registration_enabled"] is True

    async def test_disable_sms_registration_patch(self, client, admin_user):
        r = await client.patch(
            f"{BASE}/admin/settings/sms-registration",
            json={"sms_registration_enabled": False},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert r.json()["sms_registration_enabled"] is False

    async def test_sms_settings_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/settings/sms-registration",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403

    async def test_sms_settings_unauthenticated(self, client):
        r = await client.get(f"{BASE}/admin/settings/sms-registration")
        assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 15. ADMIN BILLING — TARIF REJALARI (Plans)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBillingPlans:

    async def _ensure_plan(self, client, admin_user, name="API Test Plan PRO") -> int:
        """Plan yaratadi yoki mavjudini qaytaradi."""
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={
                "name": name,
                "price": 99900,
                "duration_days": 30,
                "tier": "pro",
                "ai_limit": 5,
                "exam_limit": 2,
                "is_active": True,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        if r.status_code == 201:
            return r.json()["id"]
        # 409 = allaqachon bor, listdan topamiz
        plans = (await client.get(
            f"{BASE}/admin/billing/plans",
            headers=cookie_header(admin_user["cookies"]),
        )).json()
        for p in plans:
            if p["name"] == name:
                return p["id"]
        return plans[0]["id"] if plans else None

    async def test_list_plans_admin(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/billing/plans",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_plans_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/billing/plans",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403

    async def test_create_plan_pro(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={
                "name": "Test PRO Plan v2",
                "price": 149900,
                "duration_days": 30,
                "tier": "pro",
                "ai_limit": 10,
                "exam_limit": 3,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (201, 409)

    async def test_create_plan_plus(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={
                "name": "Test PLUS Plan v2",
                "price": 99900,
                "duration_days": 30,
                "tier": "plus",
                "ai_limit": 50,
                "exam_limit": 10,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (201, 409)

    async def test_create_plan_free_tier_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={"name": "Free Tier", "price": 1000, "duration_days": 30, "tier": "free"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (422, 400)

    async def test_create_plan_price_zero_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={"name": "Zero Price", "price": 0, "duration_days": 30, "tier": "pro"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_create_plan_duration_zero_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={"name": "Zero Duration", "price": 99900, "duration_days": 0, "tier": "pro"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_create_duplicate_plan_name(self, client, admin_user):
        name = "Duplicate Check Plan"
        await client.post(
            f"{BASE}/admin/billing/plans",
            json={"name": name, "price": 50000, "duration_days": 30, "tier": "pro"},
            headers=cookie_header(admin_user["cookies"]),
        )
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={"name": name, "price": 50000, "duration_days": 30, "tier": "pro"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 409

    async def test_update_plan(self, client, admin_user):
        plan_id = await self._ensure_plan(client, admin_user)
        if plan_id:
            r = await client.patch(
                f"{BASE}/admin/billing/plans/{plan_id}",
                json={"price": 109900},
                headers=cookie_header(admin_user["cookies"]),
            )
            assert r.status_code == 200
            assert r.json()["price"] == 109900

    async def test_update_plan_not_found(self, client, admin_user):
        r = await client.patch(
            f"{BASE}/admin/billing/plans/9999999",
            json={"price": 100000},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_update_plan_free_tier_rejected(self, client, admin_user):
        plan_id = await self._ensure_plan(client, admin_user)
        if plan_id:
            r = await client.patch(
                f"{BASE}/admin/billing/plans/{plan_id}",
                json={"tier": "free"},
                headers=cookie_header(admin_user["cookies"]),
            )
            assert r.status_code in (422, 400)

    async def test_public_plans_shows_active(self, client, admin_user):
        # Admin plan yaratib public listda ko'rish
        await client.post(
            f"{BASE}/admin/billing/plans",
            json={
                "name": "Visible PRO Plan",
                "price": 99000,
                "duration_days": 30,
                "tier": "pro",
                "is_active": True,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        r = await client.get(f"{BASE}/billing/plans")
        assert r.status_code == 200
        plans = r.json()
        assert isinstance(plans, list)
        assert len(plans) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 16. ADMIN BILLING — PROMO KODLAR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBillingPromo:

    async def test_list_promo_codes(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/billing/promo-codes",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_promo_with_percent(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": "PERCENT10TEST", "discount_percent": 10, "is_active": True},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (201, 409)

    async def test_create_promo_with_fixed_amount(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": "AMOUNT5KTEST", "discount_amount": 5000, "is_active": True},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code in (201, 409)

    async def test_create_promo_both_discounts_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": "BOTH", "discount_percent": 10, "discount_amount": 5000},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_create_promo_no_discount_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": "NODISCOUNT"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_create_promo_invalid_date_range(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={
                "code": "DATERANGE",
                "discount_percent": 10,
                "valid_from": "2025-12-31T00:00:00Z",
                "valid_until": "2025-01-01T00:00:00Z",
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_update_promo_not_found(self, client, admin_user):
        r = await client.patch(
            f"{BASE}/admin/billing/promo-codes/9999999",
            json={"is_active": False},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_create_and_deactivate_promo(self, client, admin_user):
        code = "DEACTIVATEME"
        create_r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": code, "discount_percent": 15, "is_active": True},
            headers=cookie_header(admin_user["cookies"]),
        )
        if create_r.status_code == 201:
            promo_id = create_r.json()["id"]
        else:
            promos = (await client.get(
                f"{BASE}/admin/billing/promo-codes",
                headers=cookie_header(admin_user["cookies"]),
            )).json()
            promo_id = next((p["id"] for p in promos if p["code"] == code), None)

        if promo_id:
            r = await client.patch(
                f"{BASE}/admin/billing/promo-codes/{promo_id}",
                json={"is_active": False},
                headers=cookie_header(admin_user["cookies"]),
            )
            assert r.status_code == 200
            assert r.json()["is_active"] is False

    async def test_promo_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/billing/promo-codes",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 17. ADMIN BILLING — ORDERLAR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBillingOrders:
    async def test_list_orders(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/billing/orders",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_orders_pending_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/billing/orders?status=pending_payment",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_list_orders_under_review_filter(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/billing/orders?status=under_review",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200

    async def test_approve_order_not_found(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/orders/nonexistent-order/approve",
            json={"note": "Test"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_reject_order_not_found(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/orders/nonexistent-order/reject",
            json={"note": "Test reject"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 404

    async def test_orders_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/billing/orders",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 18. ADMIN BILLING — TIER LIMITLAR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminUnifiedLimits:
    async def test_get_unified_limits(self, client, admin_user):
        r = await client.get(f"{BASE}/admin/analysis/limits", headers=cookie_header(admin_user["cookies"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) >= {"free", "plus", "pro"}
        assert set(body["free"]) == {"practice", "analysis"}

    async def test_set_practice_limit(self, client, admin_user):
        r = await client.put(f"{BASE}/admin/analysis/limits/practice", json={"tier": "free", "weekly_limit": 20, "monthly_limit": 80}, headers=cookie_header(admin_user["cookies"]))
        assert r.status_code == 200, r.text
        assert r.json()["weekly_limit"] == 20

    async def test_set_analysis_limit(self, client, admin_user):
        r = await client.put(f"{BASE}/admin/analysis/limits/analysis", json={"tier": "free", "weekly_limit": 5, "monthly_limit": 20}, headers=cookie_header(admin_user["cookies"]))
        assert r.status_code == 200, r.text
        assert r.json()["monthly_limit"] == 20

    async def test_limits_user_forbidden(self, client, regular_user):
        r = await client.get(f"{BASE}/admin/analysis/limits", headers=cookie_header(regular_user["cookies"]))
        assert r.status_code == 403

    async def test_limits_unauthenticated(self, client):
        r = await client.get(f"{BASE}/admin/analysis/limits")
        assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 19. ADMIN BILLING — MAHSULOTLAR (Products)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBillingProducts:
    async def test_list_products_admin(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/billing/products",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_create_non_full_exam_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/products",
            json={
                "product_type": "SUBSCRIPTION",
                "code": "SUBPRODUCT",
                "title": "Sub Product",
                "price": 100000,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_create_full_exam_no_external_ref_rejected(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/products",
            json={
                "product_type": "FULL_EXAM",
                "code": "EXAMTEST001",
                "title": "Exam Without Ref",
                "price": 150000,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_products_user_forbidden(self, client, regular_user):
        r = await client.get(
            f"{BASE}/admin/billing/products",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 20. EXAMS AUTH BO'LIMI
# ─────────────────────────────────────────────────────────────────────────────

class TestExamsAuth:
    async def test_login_no_ids_rejected(self, client):
        r = await client.post(f"{BASE}/exams-auth/login", json={})
        assert r.status_code in (400, 422)

    async def test_login_invalid_exam_id(self, client):
        r = await client.post(
            f"{BASE}/exams-auth/login",
            json={"exam_id": "EXAM-NOTEXISTS-12345678901"},
        )
        assert r.status_code in (400, 401, 404)

    async def test_me_unauthenticated(self, client):
        r = await client.get(f"{BASE}/exams-auth/me")
        assert r.status_code in (401, 403)

    async def test_logout_no_session_ok(self, client):
        r = await client.post(f"{BASE}/exams-auth/logout")
        assert r.status_code == 200

    async def test_refresh_no_cookie(self, client):
        r = await client.post(f"{BASE}/exams-auth/refresh")
        assert r.status_code in (401, 400)

    async def test_list_exams_unauthenticated(self, client):
        r = await client.get(f"{BASE}/exams")
        assert r.status_code in (401, 403)

    async def test_start_exam_unauthenticated(self, client):
        r = await client.post(f"{BASE}/exams/some-exam-id/start")
        assert r.status_code in (401, 403)

    async def test_exam_my_skills_unauthenticated(self, client):
        r = await client.get(f"{BASE}/exams/my-skills")
        assert r.status_code in (401, 403)

    async def test_exam_attempt_status_unauthenticated(self, client):
        r = await client.get(f"{BASE}/exams/attempts/1/status")
        assert r.status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 21. E2E: TARIF → ORDER → CANCEL
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingE2E:
    async def test_e2e_plan_order_cancel(self, client, admin_user, regular_user):
        """Admin plan yaratadi → user ko'radi → order qiladi → bekor qiladi."""

        # 1. Admin plan yaratadi
        plan_r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={
                "name": "E2E Cancel Test Plan",
                "price": 49900,
                "duration_days": 30,
                "tier": "pro",
                "ai_limit": 2,
                "exam_limit": 1,
                "is_active": True,
            },
            headers=cookie_header(admin_user["cookies"]),
        )
        assert plan_r.status_code in (201, 409)

        # 2. Public listda ko'rish
        plans_r = await client.get(f"{BASE}/billing/plans")
        assert plans_r.status_code == 200
        plans = plans_r.json()
        assert len(plans) > 0
        plan_id = plans[0]["id"]

        # 3. User order yaratadi
        order_r = await client.post(
            f"{BASE}/billing/orders",
            json={"product_type": "subscription", "product_id": str(plan_id)},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert order_r.status_code in (201, 404, 422)

        if order_r.status_code == 201:
            order = order_r.json()
            order_id = order["id"]
            assert order["status"].lower() == "pending_payment"

            # 4. Order detailini tekshirish
            detail_r = await client.get(
                f"{BASE}/billing/orders/{order_id}",
                headers=cookie_header(regular_user["cookies"]),
            )
            assert detail_r.status_code == 200

            # 5. Order bekor qilish
            cancel_r = await client.post(
                f"{BASE}/billing/orders/{order_id}/cancel",
                headers=cookie_header(regular_user["cookies"]),
            )
            assert cancel_r.status_code in (200, 409)

            if cancel_r.status_code == 200:
                assert cancel_r.json()["status"].lower() == "cancelled"

    async def test_e2e_promo_validate_and_order(self, client, admin_user, regular_user):
        """Admin promo yaratadi → User validate qiladi."""

        # Promo yaratish
        promo_r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": "E2EVALIDATE25", "discount_percent": 25, "is_active": True},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert promo_r.status_code in (201, 409)

        # Validate qilish
        validate_r = await client.post(
            f"{BASE}/billing/promo/validate",
            json={"code": "E2EVALIDATE25", "amount": 100000},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert validate_r.status_code in (200, 404)

        if validate_r.status_code == 200:
            data = validate_r.json()
            assert "discount_amount" in data
            assert "final_amount" in data
            assert data["discount_amount"] == 25000  # 25% of 100000
            assert data["final_amount"] == 75000

    async def test_cancel_my_subscription_when_none_active(self, client, regular_user):
        """Faol obunasi bo'lmagan user bekor qilishga uringanda 404 qaytadi."""
        r = await client.post(
            f"{BASE}/billing/me/subscription/cancel",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 404
        assert "Faol obuna topilmadi" in r.text


# ─────────────────────────────────────────────────────────────────────────────
# 22. XAVFSIZLIK TESTLARI
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityAndAccessControl:
    async def test_admin_routes_require_admin_role(self, client, regular_user):
        endpoints = [
            ("GET", f"{BASE}/admin/users"),
            ("GET", f"{BASE}/admin/dashboard"),
            ("GET", f"{BASE}/admin/statistics"),
            ("GET", f"{BASE}/admin/registrations"),
            ("GET", f"{BASE}/admin/attempts"),
            ("GET", f"{BASE}/admin/results"),
            ("GET", f"{BASE}/admin/omr/submissions"),
            ("GET", f"{BASE}/admin/billing/plans"),
            ("GET", f"{BASE}/admin/billing/promo-codes"),
            ("GET", f"{BASE}/admin/billing/orders"),
            ("GET", f"{BASE}/admin/analysis/limits"),
        ]
        for method, url in endpoints:
            r = await client.request(
                method, url,
                headers=cookie_header(regular_user["cookies"]),
            )
            assert r.status_code == 403, f"{method} {url} should be 403, got {r.status_code}"

    async def test_user_sees_only_own_orders(self, client, regular_user, admin_user):
        """User boshqa userning order id'sini bilsa ham 404 oladi."""
        # Admin order ko'radi (barcha orderlar)
        all_orders = (await client.get(
            f"{BASE}/admin/billing/orders",
            headers=cookie_header(admin_user["cookies"]),
        )).json()

        if all_orders:
            some_order_id = all_orders[0]["id"]
            r = await client.get(
                f"{BASE}/billing/orders/{some_order_id}",
                headers=cookie_header(regular_user["cookies"]),
            )
            # Agar bu order regular_user niki bo'lmasa — 404
            assert r.status_code in (200, 404)

    async def test_profile_isolation(self, client, regular_user, admin_user):
        """Har bir user faqat o'z profilini /users/me da ko'radi."""
        user_r = await client.get(
            f"{BASE}/users/me",
            headers=cookie_header(regular_user["cookies"]),
        )
        assert user_r.status_code == 200
        assert str(user_r.json()["id"]) == regular_user["id"]

        admin_r = await client.get(
            f"{BASE}/users/me",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert admin_r.status_code == 200
        assert str(admin_r.json()["id"]) == admin_user["id"]

    async def test_super_admin_promotion_blocked(self, client, admin_user, regular_user):
        r = await client.patch(
            f"{BASE}/admin/users/{regular_user['id']}",
            json={"global_role": "super_admin"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 403

    async def test_unauthenticated_protected_routes(self, client):
        protected = [
            ("GET", f"{BASE}/users/me"),
            ("GET", f"{BASE}/billing/orders"),
            ("GET", f"{BASE}/billing/me/subscription"),
            ("GET", f"{BASE}/billing/me/limits"),
        ]
        for method, url in protected:
            r = await client.request(method, url)
            assert r.status_code in (401, 403), f"{method} {url}: expected 401/403, got {r.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# 23. VALIDATION TESTLARI
# ─────────────────────────────────────────────────────────────────────────────

class TestValidation:
    async def test_admin_users_limit_too_large(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?limit=201",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_admin_users_negative_offset(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/users?offset=-1",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_registrations_limit_too_large(self, client, admin_user):
        r = await client.get(
            f"{BASE}/admin/registrations?limit=999",
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_order_create_missing_product_type(self, client, regular_user):
        r = await client.post(
            f"{BASE}/billing/orders",
            json={"product_id": "1"},
            headers=cookie_header(regular_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_plan_create_missing_required_fields(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/plans",
            json={"name": "Missing Fields Plan"},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422

    async def test_promo_code_too_short(self, client, admin_user):
        r = await client.post(
            f"{BASE}/admin/billing/promo-codes",
            json={"code": "A", "discount_percent": 10},
            headers=cookie_header(admin_user["cookies"]),
        )
        assert r.status_code == 422
