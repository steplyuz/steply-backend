"""Standart shablonlar (seed), shablon auto ID / o'chirish, obuna muddati hodisalari."""
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/notif_defaults.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

import app.core.models_registry  # noqa: F401  (barcha modellarni ro'yxatdan o'tkazadi)
from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import SubscriptionPlan, SubscriptionTier, UserSubscription
from app.modules.billing.subscription_notifications import scan_subscription_events
from app.modules.notifications import service
from app.modules.notifications.models import NotificationMessage, NotificationRule, NotificationTemplate
from app.modules.notifications.router import TemplateIn, create_template, delete_template

pytestmark = pytest.mark.asyncio

ADMIN = User(global_role=UserRole.ADMIN, is_active=True)


@pytest_asyncio.fixture(scope="module")
async def db_and_user():
    await init_db()
    async with dbm.AsyncSessionLocal() as s:
        user = User(global_role=UserRole.STUDENT, is_active=True)
        s.add(user)
        await s.flush()
        s.add(UserProfile(user_id=user.id, full_name="Ali Valiyev"))
        plan = SubscriptionPlan(name="Oylik Pro", price=100, duration_days=30, tier=SubscriptionTier.PRO)
        s.add(plan)
        await s.commit()
        yield s, user.id, plan.id
        await s.close()
        await dbm.engine.dispose()


async def test_seed_defaults_is_idempotent(db_and_user):
    db, _, _ = db_and_user
    first = await service.seed_defaults(db)
    assert first["templates_created"] > 0 and first["rules_created"] > 0
    second = await service.seed_defaults(db)
    assert second == {"templates_created": 0, "rules_created": 0}


async def test_seed_does_not_overwrite_admin_edits(db_and_user):
    db, _, _ = db_and_user
    tpl = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.code == "welcome_inapp"))
    tpl.body = "Admin o'zgartirgan matn"
    await db.commit()
    await service.seed_defaults(db)
    await db.refresh(tpl)
    assert tpl.body == "Admin o'zgartirgan matn"


async def test_template_auto_code_and_full_delete(db_and_user):
    db, _, _ = db_and_user
    created = await create_template(
        TemplateIn(channel="in_app", title="Test", body="salom", subject="Sarlavha"), db, ADMIN,
    )
    assert re.fullmatch(r"tpl_\d+", created["code"]), created["code"]

    # Qoidaga bog'langan shablonni o'chirish rad etiladi
    await db.merge(NotificationRule(event_code="referral_rewarded", channel="in_app", template_code=created["code"], is_active=True))
    await db.commit()
    with pytest.raises(HTTPException) as exc_info:
        await delete_template(created["code"], force=False, db=db, _=ADMIN)
    assert exc_info.value.status_code == 409

    # force=True: qoidalar bilan birga to'liq o'chadi
    await delete_template(created["code"], force=True, db=db, _=ADMIN)
    assert await db.scalar(select(NotificationTemplate.id).where(NotificationTemplate.code == created["code"])) is None
    assert await db.scalar(select(NotificationRule.id).where(NotificationRule.template_code == created["code"])) is None


async def test_manual_code_still_works_and_rejects_duplicates(db_and_user):
    db, _, _ = db_and_user
    body = TemplateIn(code="custom_hello", channel="in_app", title="Salom", body="matn")
    out = await create_template(body, db, ADMIN)
    assert out["code"] == "custom_hello"
    with pytest.raises(HTTPException) as exc_info:
        await create_template(body, db, ADMIN)
    assert exc_info.value.status_code == 409


async def test_expiring_reminder_is_sent_once(db_and_user):
    db, user_id, plan_id = db_and_user
    now = datetime.now(timezone.utc)
    sub = UserSubscription(
        user_id=user_id, plan_id=plan_id, start_date=now - timedelta(days=28),
        end_date=now + timedelta(days=2, hours=-1), is_active=True,
    )
    db.add(sub)
    await db.commit()

    stats = await scan_subscription_events(db, now=now)
    assert stats["expiring_checked"] >= 1

    msgs = (await db.execute(select(NotificationMessage).where(
        NotificationMessage.template_code == "subscription_expiring_inapp",
        NotificationMessage.user_id == user_id,
    ))).scalars().all()
    assert len(msgs) == 1, "3 kunlik ogohlantirish bir marta yuborilishi kerak"
    assert "Oylik Pro" in msgs[0].body
    assert "2 kun" in msgs[0].body

    # Takror skan dublikat xabar yubormasligi kerak
    await scan_subscription_events(db, now=now)
    again = (await db.execute(select(NotificationMessage).where(
        NotificationMessage.template_code == "subscription_expiring_inapp",
        NotificationMessage.user_id == user_id,
    ))).scalars().all()
    assert len(again) == 1


async def test_expired_notice_skipped_when_another_subscription_active(db_and_user):
    db, user_id, plan_id = db_and_user
    now = datetime.now(timezone.utc)
    ended = UserSubscription(
        user_id=user_id, plan_id=plan_id, start_date=now - timedelta(days=31),
        end_date=now - timedelta(hours=2), is_active=True,
    )
    still = UserSubscription(
        user_id=user_id, plan_id=plan_id, start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=20), is_active=True,
    )
    db.add_all([ended, still])
    await db.commit()

    await scan_subscription_events(db, now=now)
    msgs = (await db.execute(select(NotificationMessage).where(
        NotificationMessage.template_code == "subscription_expired_inapp",
        NotificationMessage.user_id == user_id,
    ))).scalars().all()
    assert msgs == [], "boshqa faol obuna bo'lsa 'tugadi' xabari yuborilmasligi kerak"
