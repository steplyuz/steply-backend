"""Xabarnomalar servisi: shablon, qabul qiluvchi, navbat, hodisalar, opt-out va yuborish.

Oqim: queue_send()/emit_event() -> notification_messages (queued) -> process_due() -> sent/failed.
Qayta urinish: 1, 2, 4 ... daqiqa (1 soatgacha), max_attempts gacha.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.keys import derive_key
from app.modules.auth.models import ContactType, User, UserContact, UserProfile
from app.modules.notifications import channels, email_layout
from app.modules.notifications.channels import PermanentChannelError
from app.modules.notifications.gmail import GmailAuthError
from app.modules.notifications.models import (
    EmailAccount, EmailAccountStatus, NotificationCategory, NotificationChannel, NotificationMessage,
    NotificationOptOut, NotificationRule, NotificationStatus, NotificationTemplate, UserNotification,
)

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_BATCH_RECIPIENTS = 500

# Tizim hodisalari. Admin har biri uchun kanal va shablonni /rules orqali belgilaydi.
EVENTS: dict[str, str] = {
    "user_registered": "Yangi foydalanuvchi ro'yxatdan o'tdi",
    "order_paid": "Buyurtma to'landi / tasdiqlandi",
    "referral_rewarded": "Referal mukofoti berildi",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Shablon ----------

def placeholders(text: str) -> set[str]:
    return set(_VAR_RE.findall(text or ""))


def render(text: str, variables: dict[str, Any]) -> str:
    """{{name}} o'rinlarini qiymatlar bilan almashtiradi. Yo'q o'zgaruvchi -> ValueError."""
    missing = placeholders(text) - set(variables)
    if missing:
        raise ValueError(f"Qiymat berilmagan o'zgaruvchilar: {', '.join(sorted(missing))}")
    return _VAR_RE.sub(lambda m: str(variables[m.group(1)]), text)


def backoff_seconds(attempts: int) -> int:
    """attempts=1 -> 60s, 2 -> 120s, 3 -> 240s ... 3600s dan oshmaydi."""
    return min(60 * (2 ** max(attempts - 1, 0)), 3600)


# ---------- Unsubscribe ----------

_UNSUB_PURPOSE = "notif-unsubscribe"


def make_unsubscribe_token(channel: str, recipient: str) -> str:
    return jwt.encode(
        {"purpose": _UNSUB_PURPOSE, "ch": channel, "rcp": recipient},
        derive_key(_UNSUB_PURPOSE), algorithm="HS256",
    )


def parse_unsubscribe_token(token: str) -> tuple[str, str]:
    try:
        payload = jwt.decode(token, derive_key(_UNSUB_PURPOSE), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("Havola noto'g'ri") from exc
    if payload.get("purpose") != _UNSUB_PURPOSE:
        raise ValueError("Havola noto'g'ri")
    return str(payload["ch"]), str(payload["rcp"])


def build_unsubscribe_url(channel: str, recipient: str) -> str:
    base = (settings.API_PUBLIC_URL or f"https://api.{settings.AUTH_DOMAIN}").rstrip("/")
    return f"{base}/api/v1/notifications/unsubscribe?token={make_unsubscribe_token(channel, recipient)}"


async def is_opted_out(db: AsyncSession, channel: str, recipient: str) -> bool:
    return await db.scalar(select(NotificationOptOut.id).where(
        NotificationOptOut.channel == channel, NotificationOptOut.recipient == recipient,
    )) is not None


async def add_optout(db: AsyncSession, channel: str, recipient: str) -> None:
    if not await is_opted_out(db, channel, recipient):
        db.add(NotificationOptOut(channel=channel, recipient=recipient))
        await db.flush()


# ---------- Qabul qiluvchi ----------

def validate_recipient(channel: str, recipient: str) -> str:
    recipient = (recipient or "").strip()
    if channel == NotificationChannel.IN_APP:
        try:
            return str(uuid.UUID(recipient))
        except ValueError as exc:
            raise HTTPException(422, "in_app uchun user id (UUID) kerak") from exc
    if channel == NotificationChannel.SMS:
        from app.modules.auth.sms import SmsService
        try:
            return SmsService._normalize_phone(recipient)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if channel == NotificationChannel.TELEGRAM:
        if not recipient.lstrip("-").isdigit():
            raise HTTPException(422, "Telegram chat_id raqam bo'lishi kerak")
        return recipient
    if channel == NotificationChannel.EMAIL:
        if not _EMAIL_RE.match(recipient):
            raise HTTPException(422, f"Email noto'g'ri: {recipient}")
        return recipient.lower()
    raise HTTPException(422, "Noma'lum kanal")


async def resolve_user_recipient(db: AsyncSession, user_id: uuid.UUID, channel: str) -> tuple[str, Optional[str]]:
    """User uchun manzil va to'liq ism (shablon {{name}} uchun)."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, f"Foydalanuvchi topilmadi: {user_id}")
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    full_name = profile.full_name if profile else None

    if channel == NotificationChannel.IN_APP:
        return str(user_id), full_name
    if channel == NotificationChannel.TELEGRAM:
        if not user.telegram_user_id:
            raise HTTPException(422, f"Foydalanuvchida Telegram ulanmagan: {user_id}")
        return str(user.telegram_user_id), full_name

    ctype = ContactType.EMAIL if channel == NotificationChannel.EMAIL else ContactType.PHONE
    value = await db.scalar(
        select(UserContact.value)
        .where(UserContact.user_id == user_id, UserContact.contact_type == ctype)
        .order_by(UserContact.is_verified.desc(), UserContact.is_primary.desc(), UserContact.id)
        .limit(1)
    )
    if value is None:
        label = "email" if ctype == ContactType.EMAIL else "telefon"
        raise HTTPException(422, f"Foydalanuvchida {label} yo'q: {user_id}")
    return value, full_name


# ---------- Email hisoblari ----------

async def default_email_account(db: AsyncSession) -> EmailAccount | None:
    return await db.scalar(
        select(EmailAccount).where(
            EmailAccount.is_default.is_(True),
            EmailAccount.is_active.is_(True),
            EmailAccount.status == EmailAccountStatus.OK,
        ).limit(1)
    )


async def _pick_email_account(db: AsyncSession, account_id: int | None) -> EmailAccount | None:
    if account_id is not None:
        account = await db.get(EmailAccount, account_id)
        if account is None or not account.is_active:
            raise PermanentChannelError("Email hisob topilmadi yoki o'chirilgan")
        if account.status != EmailAccountStatus.OK:
            raise GmailAuthError(account.id, "Email hisob qayta ulanishi kerak")
        return account
    return await default_email_account(db)  # None bo'lsa Resend fallback


# ---------- Shablon va navbat ----------

async def get_template(db: AsyncSession, code: str) -> NotificationTemplate:
    tpl = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.code == code))
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    if not tpl.is_active:
        raise HTTPException(409, "Shablon faol emas")
    return tpl


async def enqueue(
    db: AsyncSession,
    *,
    channel: str,
    recipient: str,
    body: str,
    subject: str | None = None,
    user_id: uuid.UUID | None = None,
    template_code: str | None = None,
    idempotency_key: str | None = None,
    created_by: uuid.UUID | None = None,
    send_at: datetime | None = None,
    category: str = NotificationCategory.TRANSACTIONAL,
    email_account_id: int | None = None,
    link: str | None = None,
    html_body: str | None = None,
) -> NotificationMessage:
    if idempotency_key:
        existing = await db.scalar(
            select(NotificationMessage).where(NotificationMessage.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing  # takroriy so'rov: yangi xabar yaratilmaydi
    msg = NotificationMessage(
        channel=channel, recipient=recipient, body=body, subject=subject, user_id=user_id,
        template_code=template_code, idempotency_key=idempotency_key, created_by=created_by,
        category=category, email_account_id=email_account_id, link=link, html_body=html_body,
        status=NotificationStatus.QUEUED, attempts=0, next_attempt_at=send_at or _now(),
    )
    db.add(msg)
    await db.flush()
    return msg


async def queue_send(
    db: AsyncSession,
    *,
    channel: str,
    template_code: str | None,
    subject: str | None,
    body: str | None,
    user_ids: list[uuid.UUID],
    recipients: list[str],
    variables: dict[str, Any],
    send_at: datetime | None,
    idempotency_key: str | None,
    created_by: uuid.UUID | None,
    category: str = NotificationCategory.TRANSACTIONAL,
    email_account_id: int | None = None,
    link: str | None = None,
    html_body: str | None = None,
) -> tuple[list[NotificationMessage], int]:
    """Qabul qiluvchilarga xabar navbatga qo'yadi. (xabarlar, o'tkazib yuborilganlar soni) qaytaradi.

    Hammasi yoki hech narsa: render/manzil xatosi bo'lsa hech narsa yozilmaydi.
    Marketing xabarda opt-out qilganlarga yuborilmaydi va unsubscribe havolasi qo'shiladi.
    """
    if channel not in NotificationChannel.ALL:
        raise HTTPException(422, "Noma'lum kanal")
    if category not in (NotificationCategory.TRANSACTIONAL, NotificationCategory.MARKETING):
        raise HTTPException(422, "category: transactional yoki marketing bo'lishi kerak")
    total = len(user_ids) + len(recipients)
    if total == 0:
        raise HTTPException(422, "Qabul qiluvchi yo'q (user_ids yoki recipients)")
    if total > MAX_BATCH_RECIPIENTS:
        raise HTTPException(422, f"Bir so'rovda ko'pi bilan {MAX_BATCH_RECIPIENTS} qabul qiluvchi")
    if channel == NotificationChannel.IN_APP and recipients:
        raise HTTPException(422, "in_app faqat user_ids orqali yuboriladi (recipients emas)")
    if email_account_id is not None:
        if channel != NotificationChannel.EMAIL:
            raise HTTPException(422, "email_account_id faqat email kanal uchun")
        account = await db.get(EmailAccount, email_account_id)
        if account is None or not account.is_active:
            raise HTTPException(422, "Email hisob topilmadi yoki faol emas")
    if channel == NotificationChannel.EMAIL and template_code is None and not subject:
        raise HTTPException(422, "Email uchun subject kerak")

    tpl: NotificationTemplate | None = None
    if template_code:
        tpl = await get_template(db, template_code)
        if tpl.channel != channel:
            raise HTTPException(422, f"Shablon '{tpl.code}' kanali {tpl.channel}, so'ralgan: {channel}")
        body_tpl, subject_tpl = tpl.body, tpl.subject
        html_tpl = tpl.html_body
    else:
        if not body:
            raise HTTPException(422, "template_code yoki body kerak")
        body_tpl, subject_tpl = body, subject
        html_tpl = html_body

    try:
        email_layout.validate_html(html_tpl)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    # 1) Barcha qabul qiluvchilarni va matnlarni tayyorlaymiz (xato bo'lsa hech narsa yozilmaydi)
    prepared: list[dict] = []
    for uid in user_ids:
        address, full_name = await resolve_user_recipient(db, uid, channel)
        prepared.append({"recipient": address, "user_id": uid, "vars": {"name": full_name or "", **variables}})
    for raw in recipients:
        prepared.append({"recipient": validate_recipient(channel, raw), "user_id": None, "vars": dict(variables)})

    for item in prepared:
        try:
            item["body"] = render(body_tpl, item["vars"])
            item["subject"] = render(subject_tpl, item["vars"]) if subject_tpl else None
            item["html"] = render(html_tpl, item["vars"]) if html_tpl else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if channel == NotificationChannel.EMAIL and not item["subject"]:
            raise HTTPException(422, "Email uchun subject kerak")

    # 2) Navbatga qo'yamiz
    messages: list[NotificationMessage] = []
    skipped = 0
    for idx, item in enumerate(prepared):
        if category == NotificationCategory.MARKETING:
            if await is_opted_out(db, channel, item["recipient"]):
                skipped += 1
                continue
            if channel != NotificationChannel.IN_APP:  # ilova ichida bekor qilish havolasi kerak emas
                url = build_unsubscribe_url(channel, item["recipient"])
                item["body"] = f"{item['body']}\n\n---\nObunani bekor qilish: {url}"
        key = f"{idempotency_key}:{idx}" if idempotency_key else None
        messages.append(await enqueue(
            db,
            channel=channel,
            recipient=item["recipient"],
            body=item["body"],
            subject=item["subject"],
            user_id=item["user_id"],
            template_code=tpl.code if tpl else None,
            idempotency_key=key,
            created_by=created_by,
            send_at=send_at,
            category=category,
            email_account_id=email_account_id,
            link=link,
            html_body=item["html"],
        ))
    return messages, skipped


async def emit_event(
    db: AsyncSession,
    event_code: str,
    *,
    user_id: uuid.UUID,
    ref: str,
    variables: dict[str, Any] | None = None,
) -> int:
    """Tizim hodisasi: shu hodisaga bog'langan barcha faol qoidalar bo'yicha xabar navbatga qo'yadi.

    Hech qachon exception tashlamaydi — asosiy jarayon (ro'yxatdan o'tish, to'lov) buzilmasin.
    ref: hodisa identifikatori (takror yuborilmasligi uchun), masalan "order-<id>".
    """
    if event_code not in EVENTS:
        logger.error("notification: noma'lum hodisa %s", event_code)
        return 0
    rules = (await db.execute(
        select(NotificationRule).where(
            NotificationRule.event_code == event_code, NotificationRule.is_active.is_(True),
        )
    )).scalars().all()

    queued = 0
    for rule in rules:
        try:
            # Savepoint: bitta kanal xatosi boshqa kanallarni va asosiy tranzaksiyani buzmasin
            async with db.begin_nested():
                messages, _ = await queue_send(
                    db,
                    channel=rule.channel,
                    template_code=rule.template_code,
                    subject=None,
                    body=None,
                    user_ids=[user_id],
                    recipients=[],
                    variables=dict(variables or {}),
                    send_at=None,
                    idempotency_key=f"evt:{event_code}:{rule.channel}:{ref}",
                    created_by=None,
                )
            queued += len(messages)
        except Exception as exc:  # noqa: BLE001 — hodisa xatosi asosiy jarayonni to'xtatmasin
            detail = exc.detail if isinstance(exc, HTTPException) else type(exc).__name__
            logger.info("notification %s/%s o'tkazib yuborildi: %s", event_code, rule.channel, detail)
    return queued


# ---------- Yuborish (worker) ----------

async def _deliver_in_app(db: AsyncSession, msg: NotificationMessage) -> None:
    """Ilova ichidagi bildirishnoma: foydalanuvchi inbox'iga yozadi (tashqi API yo'q)."""
    db.add(UserNotification(
        user_id=uuid.UUID(msg.recipient),
        title=(msg.subject or "Bildirishnoma")[:255],
        body=msg.body,
        link=msg.link,
        category=msg.category,
        message_id=msg.id,
    ))
    await db.flush()


async def process_due(db: AsyncSession, batch_size: int = 50) -> dict[str, int]:
    """Vaqti kelgan queued xabarlarni yuboradi. Har bir xabar alohida commit qilinadi."""
    now = _now()
    rows = (await db.execute(
        select(NotificationMessage)
        .where(
            NotificationMessage.status == NotificationStatus.QUEUED,
            NotificationMessage.next_attempt_at <= now,
        )
        .order_by(NotificationMessage.next_attempt_at, NotificationMessage.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )).scalars().all()

    stats = {"picked": len(rows), "sent": 0, "failed": 0, "retry": 0}
    for msg in rows:
        msg.attempts += 1
        account: EmailAccount | None = None
        try:
            if msg.channel == NotificationChannel.IN_APP:
                await _deliver_in_app(db, msg)
            else:
                if msg.channel == NotificationChannel.EMAIL:
                    account = await _pick_email_account(db, msg.email_account_id)
                unsubscribe = None
                if msg.category == NotificationCategory.MARKETING and msg.channel == NotificationChannel.EMAIL:
                    unsubscribe = build_unsubscribe_url(msg.channel, msg.recipient)
                msg.provider_message_id = await channels.send(
                    msg.channel, msg.recipient, msg.subject, msg.body,
                    idempotency_key=f"notif-{msg.id}",
                    email_account=account,
                    list_unsubscribe=unsubscribe,
                    html=(email_layout.wrap(msg.html_body or email_layout.text_to_html(msg.body), unsubscribe)
                          if msg.channel == NotificationChannel.EMAIL else None),
                )
            msg.status = NotificationStatus.SENT
            msg.sent_at = _now()
            msg.last_error = None
            stats["sent"] += 1
        except GmailAuthError as exc:
            # Gmail ulanishi uzilgan: hisobni belgilaymiz, admin qayta ulaguncha shu hisob ishlatilmaydi
            if account is not None:
                account.status = EmailAccountStatus.REAUTH_REQUIRED
                account.last_error = str(exc)[:500]
            msg.status = NotificationStatus.FAILED
            msg.last_error = str(exc)[:1000]
            stats["failed"] += 1
        except PermanentChannelError as exc:
            msg.status = NotificationStatus.FAILED
            msg.last_error = str(exc)[:1000]
            stats["failed"] += 1
        except Exception as exc:  # vaqtinchalik xatolar va kutilmagan xatolar
            msg.last_error = (str(exc) or type(exc).__name__)[:1000]
            if msg.attempts >= msg.max_attempts:
                msg.status = NotificationStatus.FAILED
                stats["failed"] += 1
            else:
                msg.next_attempt_at = _now() + timedelta(seconds=backoff_seconds(msg.attempts))
                stats["retry"] += 1
            logger.warning("notification %s urinish %s xato: %s", msg.id, msg.attempts, msg.last_error)
        await db.commit()
    return stats


async def retry_message(db: AsyncSession, msg: NotificationMessage) -> NotificationMessage:
    if msg.status not in (NotificationStatus.FAILED, NotificationStatus.CANCELLED):
        raise HTTPException(409, "Faqat failed yoki cancelled xabarni qayta yuborish mumkin")
    msg.status = NotificationStatus.QUEUED
    msg.attempts = 0
    msg.next_attempt_at = _now()
    await db.flush()
    return msg


async def cancel_message(db: AsyncSession, msg: NotificationMessage) -> NotificationMessage:
    if msg.status != NotificationStatus.QUEUED:
        raise HTTPException(409, "Faqat navbatdagi (queued) xabarni bekor qilish mumkin")
    msg.status = NotificationStatus.CANCELLED
    await db.flush()
    return msg


async def stats_by_status(db: AsyncSession) -> dict[str, dict[str, int]]:
    rows = (await db.execute(
        select(NotificationMessage.channel, NotificationMessage.status, func.count(NotificationMessage.id))
        .group_by(NotificationMessage.channel, NotificationMessage.status)
    )).all()
    out: dict[str, dict[str, int]] = {}
    for channel, status, n in rows:
        out.setdefault(channel, {})[status] = int(n)
    return out


# ---------- Broadcast: barcha faol foydalanuvchilarga ----------

BROADCAST_BATCH = 500


async def _eligible_user_ids(db: AsyncSession, channel: str, user_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Shu kanal bo'yicha manzili bor foydalanuvchilar (manzili yo'qlari o'tkazib yuboriladi)."""
    if channel == NotificationChannel.IN_APP:
        return list(user_ids)
    if channel == NotificationChannel.TELEGRAM:
        rows = await db.execute(select(User.id).where(User.id.in_(user_ids), User.telegram_user_id.is_not(None)))
        return list(rows.scalars().all())
    ctype = ContactType.EMAIL if channel == NotificationChannel.EMAIL else ContactType.PHONE
    rows = await db.execute(
        select(UserContact.user_id)
        .where(UserContact.user_id.in_(user_ids), UserContact.contact_type == ctype)
        .distinct()
    )
    return list(rows.scalars().all())


async def broadcast(
    db: AsyncSession,
    *,
    channel_list: list[str],
    template_code: str | None,
    subject: str | None,
    body: str | None,
    html_body: str | None,
    link: str | None,
    variables: dict[str, Any],
    category: str,
    email_account_id: int | None,
    created_by: uuid.UUID | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Barcha faol foydalanuvchilarga bir vaqtda bir nechta kanal orqali xabar navbatga qo'yadi.

    Hammasi bitta so'rov tranzaksiyasida: xato bo'lsa hech narsa yozilmaydi. Userlar BROADCAST_BATCH
    tadan bo'lib ishlanadi. Shablon tanlansa, kanal shablon kanali bilan bir xil bo'lishi kerak.
    """
    if template_code:
        tpl = await get_template(db, template_code)
        if channel_list != [tpl.channel]:
            raise HTTPException(422, f"Shablon kanali {tpl.channel}: faqat shu kanalni tanlang")

    summary: dict[str, Any] = {
        "queued": 0,
        "skipped_opted_out": 0,
        "skipped_no_address": 0,
        "per_channel": {c: 0 for c in channel_list},
    }
    offset = 0
    chunk_no = 0
    while True:
        batch = list((await db.execute(
            select(User.id).where(User.is_active.is_(True)).order_by(User.id).offset(offset).limit(BROADCAST_BATCH)
        )).scalars().all())
        if not batch:
            break
        offset += len(batch)
        for ch in channel_list:
            eligible = await _eligible_user_ids(db, ch, batch)
            summary["skipped_no_address"] += len(batch) - len(eligible)
            if not eligible:
                continue
            key = f"{idempotency_key}:{ch}:{chunk_no}" if idempotency_key else None
            msgs, skipped = await queue_send(
                db,
                channel=ch,
                template_code=template_code,
                subject=subject,
                body=body,
                user_ids=eligible,
                recipients=[],
                variables=dict(variables),
                send_at=None,
                idempotency_key=key,
                created_by=created_by,
                category=category,
                email_account_id=email_account_id if ch == NotificationChannel.EMAIL else None,
                link=link if ch == NotificationChannel.IN_APP else None,
                html_body=html_body if ch == NotificationChannel.EMAIL else None,
            )
            summary["queued"] += len(msgs)
            summary["skipped_opted_out"] += skipped
            summary["per_channel"][ch] += len(msgs)
        chunk_no += 1
    return summary
