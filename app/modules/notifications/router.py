"""Xabarnomalar API.

* admin_router (/admin/notifications): shablonlar, yuborish, navbat, hodisa qoidalari, email hisoblar.
* internal_router (/internal/notifications): boshqa servislar uchun (X-Internal-Token).
* public_router (/notifications): unsubscribe havolasi (token bilan, login talab qilinmaydi).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.internal_auth import verify_internal_token
from app.modules.auth.dependencies import require_role
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.notifications import email_layout, gmail, service
from app.modules.notifications.channels import PermanentChannelError, TransientChannelError
from app.modules.notifications.models import (
    EmailAccount, EmailAccountStatus, NotificationCategory, NotificationChannel, NotificationMessage,
    NotificationRule, NotificationStatus, NotificationTemplate, UserNotification,
)

admin_router = APIRouter(prefix="/admin/notifications", tags=["Admin Notifications"])
internal_router = APIRouter(
    prefix="/internal/notifications",
    tags=["Internal Notifications"],
    dependencies=[Depends(verify_internal_token)],
)
public_router = APIRouter(prefix="/notifications", tags=["Notifications"])
ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)
ChannelT = Literal["sms", "telegram", "email", "in_app"]
CategoryT = Literal["transactional", "marketing"]
_admin_dep = Depends(require_role(*ADMIN_ROLES))


# ---------- Schemalar ----------

class TemplateIn(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    channel: ChannelT
    title: str = Field(min_length=1, max_length=200)
    subject: Optional[str] = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    # Email uchun HTML (ixtiyoriy). Steply brendi bilan avtomatik o'raladi.
    html_body: Optional[str] = Field(default=None, max_length=50000)
    is_active: bool = True


class TemplatePatch(BaseModel):
    # code va channel o'zgarmaydi (mavjud xabarlar va qoidalar bilan bog'liq)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    subject: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = Field(default=None, min_length=1, max_length=10000)
    html_body: Optional[str] = Field(default=None, max_length=50000)
    is_active: Optional[bool] = None


class TemplatePreviewIn(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)


class SendIn(BaseModel):
    channel: ChannelT
    template_code: Optional[str] = Field(default=None, max_length=64)
    subject: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = Field(default=None, max_length=10000)
    user_ids: list[uuid.UUID] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)
    # transactional: doim yuboriladi. marketing: opt-out qilganlarga yuborilmaydi + unsubscribe havolasi
    category: CategoryT = "transactional"
    # Email uchun aniq Gmail hisob. None = Resend (korporativ domen, default)
    email_account_id: Optional[int] = None
    # in_app uchun: bildirishnoma bosilganda ochiladigan havola (masalan /exams)
    link: Optional[str] = Field(default=None, max_length=500)
    send_at: Optional[datetime] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=100)


class BroadcastIn(BaseModel):
    """Barcha faol foydalanuvchilarga bir vaqtda yuborish (in_app va/yoki email va boshqalar)."""

    channels: list[ChannelT] = Field(min_length=1, max_length=4)
    template_code: Optional[str] = Field(default=None, max_length=64)
    subject: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = Field(default=None, max_length=10000)
    html_body: Optional[str] = Field(default=None, max_length=50000)
    link: Optional[str] = Field(default=None, max_length=500)
    variables: dict[str, str] = Field(default_factory=dict)
    category: CategoryT = "transactional"
    email_account_id: Optional[int] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=100)


class RuleIn(BaseModel):
    event_code: str = Field(min_length=3, max_length=64)
    channel: ChannelT
    template_code: str = Field(min_length=3, max_length=64)
    is_active: bool = True


class EmailAccountPatch(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class TestEmailIn(BaseModel):
    to: str = Field(max_length=255)
    subject: str = Field(default="Steply test xabari", max_length=255)
    body: str = Field(default="Bu test xabari.", max_length=5000)


# ---------- Yordamchilar ----------

def _tpl_out(t: NotificationTemplate) -> dict:
    return {
        "code": t.code,
        "channel": t.channel,
        "title": t.title,
        "subject": t.subject,
        "body": t.body,
        "html_body": t.html_body,
        "is_active": t.is_active,
        "placeholders": sorted(service.placeholders(t.body) | service.placeholders(t.subject or "")),
    }


def _msg_out(m: NotificationMessage) -> dict:
    return {
        "id": m.id,
        "channel": m.channel,
        "recipient": m.recipient,
        "user_id": str(m.user_id) if m.user_id else None,
        "template_code": m.template_code,
        "subject": m.subject,
        "body": m.body,
        "category": m.category,
        "email_account_id": m.email_account_id,
        "link": m.link,
        "status": m.status,
        "attempts": m.attempts,
        "max_attempts": m.max_attempts,
        "next_attempt_at": m.next_attempt_at,
        "last_error": m.last_error,
        "provider_message_id": m.provider_message_id,
        "created_at": m.created_at,
        "sent_at": m.sent_at,
    }


def _rule_out(r: NotificationRule) -> dict:
    return {
        "event_code": r.event_code,
        "channel": r.channel,
        "template_code": r.template_code,
        "is_active": r.is_active,
    }


def _account_out(a: EmailAccount) -> dict:
    return {
        "id": a.id,
        "provider": a.provider,
        "email": a.email,
        "display_name": a.display_name,
        "is_default": a.is_default,
        "is_active": a.is_active,
        "status": a.status,
        "last_error": a.last_error,
        "created_at": a.created_at,
    }


async def _queue(db: AsyncSession, data: SendIn, created_by: uuid.UUID | None) -> dict:
    messages, skipped = await service.queue_send(
        db,
        channel=data.channel,
        template_code=data.template_code,
        subject=data.subject,
        body=data.body,
        user_ids=data.user_ids,
        recipients=data.recipients,
        variables=data.variables,
        send_at=data.send_at,
        idempotency_key=data.idempotency_key,
        created_by=created_by,
        category=data.category,
        email_account_id=data.email_account_id,
        link=data.link,
    )
    await db.commit()
    return {
        "queued": len(messages),
        "skipped_opted_out": skipped,
        "ids": [m.id for m in messages],
        "status": NotificationStatus.QUEUED,
    }


async def _get_message(db: AsyncSession, message_id: int) -> NotificationMessage:
    msg = await db.get(NotificationMessage, message_id)
    if msg is None:
        raise HTTPException(404, "Xabar topilmadi")
    return msg


async def _get_account(db: AsyncSession, account_id: int) -> EmailAccount:
    account = await db.get(EmailAccount, account_id)
    if account is None:
        raise HTTPException(404, "Email hisob topilmadi")
    return account


# ---------- Admin: shablonlar ----------

@admin_router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    rows = (await db.execute(select(NotificationTemplate).order_by(NotificationTemplate.code))).scalars().all()
    return [_tpl_out(t) for t in rows]


@admin_router.post("/templates", status_code=201)
async def create_template(data: TemplateIn, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    if data.channel == "email" and not data.subject:
        raise HTTPException(422, "Email shablon uchun subject kerak")
    try:
        email_layout.validate_html(data.html_body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if await db.scalar(select(NotificationTemplate.id).where(NotificationTemplate.code == data.code)):
        raise HTTPException(409, "Bu kod bilan shablon allaqachon bor")
    tpl = NotificationTemplate(**data.model_dump())
    db.add(tpl)
    await db.commit()
    return _tpl_out(tpl)


@admin_router.patch("/templates/{code}")
async def update_template(code: str, data: TemplatePatch, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    tpl = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.code == code))
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    changes = data.model_dump(exclude_unset=True)
    try:
        email_layout.validate_html(changes.get("html_body"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if "subject" in changes and tpl.channel == "email" and not changes["subject"]:
        raise HTTPException(422, "Email shablon uchun subject bo'sh bo'lmasligi kerak")
    for key, value in changes.items():
        setattr(tpl, key, value)
    await db.commit()
    return _tpl_out(tpl)


@admin_router.post("/templates/{code}/preview")
async def preview_template(code: str, data: TemplatePreviewIn, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    tpl = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.code == code))
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    # {{name}} yuborishda foydalanuvchi ismi bilan to'ldiriladi; ko'rib chiqishda namuna qiymat
    variables = {"name": "Foydalanuvchi", **data.variables}
    try:
        return {
            "subject": service.render(tpl.subject, variables) if tpl.subject else None,
            "body": service.render(tpl.body, variables),
            "html": email_layout.wrap(
                service.render(tpl.html_body, variables) if tpl.html_body else email_layout.text_to_html(service.render(tpl.body, variables)),
                None,
            ),
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------- Admin: hodisa qoidalari ----------

@admin_router.get("/events")
async def list_events(_: User = _admin_dep):
    return [{"event_code": code, "description": text} for code, text in service.EVENTS.items()]


@admin_router.get("/rules")
async def list_rules(db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    rows = (await db.execute(select(NotificationRule).order_by(NotificationRule.event_code, NotificationRule.channel))).scalars().all()
    return [_rule_out(r) for r in rows]


@admin_router.put("/rules")
async def upsert_rule(data: RuleIn, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    """Hodisani kanal + shablonga bog'laydi (mavjud bo'lsa yangilaydi)."""
    if data.event_code not in service.EVENTS:
        raise HTTPException(422, f"Noma'lum hodisa. Ruxsat etilganlar: {', '.join(service.EVENTS)}")
    tpl = await db.scalar(select(NotificationTemplate).where(NotificationTemplate.code == data.template_code))
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    if tpl.channel != data.channel:
        raise HTTPException(422, f"Shablon kanali {tpl.channel}, qoida kanali {data.channel}")
    rule = await db.scalar(select(NotificationRule).where(
        NotificationRule.event_code == data.event_code, NotificationRule.channel == data.channel,
    ))
    if rule is None:
        rule = NotificationRule(event_code=data.event_code, channel=data.channel)
        db.add(rule)
    rule.template_code = data.template_code
    rule.is_active = data.is_active
    await db.commit()
    return _rule_out(rule)


@admin_router.delete("/rules/{event_code}/{channel}", status_code=204)
async def delete_rule(event_code: str, channel: ChannelT, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    rule = await db.scalar(select(NotificationRule).where(
        NotificationRule.event_code == event_code, NotificationRule.channel == channel,
    ))
    if rule is None:
        raise HTTPException(404, "Qoida topilmadi")
    await db.delete(rule)
    await db.commit()


# ---------- Admin: email hisoblar (Gmail) ----------

@admin_router.get("/email-accounts")
async def list_email_accounts(db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    rows = (await db.execute(select(EmailAccount).order_by(EmailAccount.id))).scalars().all()
    return [_account_out(a) for a in rows]


@admin_router.get("/email-accounts/gmail/connect")
async def gmail_connect(_: User = _admin_dep):
    """Google consent sahifasi URL'ini qaytaradi. Frontend shu URL ga yo'naltiradi."""
    try:
        url = gmail.build_connect_url(_.id)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"url": url}


@admin_router.get("/email-accounts/gmail/callback")
async def gmail_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    admin: User = _admin_dep,
):
    """Google redirect'i. Refresh token shifrlanib saqlanadi, so'ng admin panelga qaytariladi."""
    target = f"{settings.FRONTEND_URL.rstrip('/')}/admin/notifications"
    if error or not code or not state:
        return RedirectResponse(f"{target}?gmail=cancelled")
    try:
        state_admin = gmail.verify_state(state)
    except ValueError:
        return RedirectResponse(f"{target}?gmail=error")
    if state_admin != admin.id:
        # Boshqa admin boshlagan oqim: qabul qilinmaydi (CSRF himoyasi)
        return RedirectResponse(f"{target}?gmail=error")
    try:
        tokens = await gmail.exchange_code(code)
        email, name = await gmail.fetch_profile(tokens["access_token"])
    except (ValueError, KeyError, httpx.HTTPError):
        # Google tarmog'i xatolari ham admin panelga tushsin, 500 emas
        return RedirectResponse(f"{target}?gmail=error")
    refresh = tokens.get("refresh_token")
    if not refresh:
        return RedirectResponse(f"{target}?gmail=no_refresh_token")

    account = await db.scalar(select(EmailAccount).where(EmailAccount.email == email))
    if account is None:
        account = EmailAccount(provider="gmail", email=email)
        db.add(account)
    account.display_name = name or account.display_name
    account.refresh_token_encrypted = gmail.encrypt_token(refresh)
    account.is_active = True
    account.status = EmailAccountStatus.OK
    account.last_error = None
    await db.flush()
    has_default = await service.default_email_account(db)
    if has_default is None:
        account.is_default = True
    await db.commit()
    return RedirectResponse(f"{target}?gmail=connected")


@admin_router.patch("/email-accounts/{account_id}")
async def update_email_account(
    account_id: int, data: EmailAccountPatch, db: AsyncSession = Depends(get_db), _: User = _admin_dep,
):
    account = await _get_account(db, account_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_default") is True:
        if not account.is_active or account.status != EmailAccountStatus.OK:
            raise HTTPException(409, "Faol va ulangan hisobnigina default qilish mumkin")
        others = (await db.execute(select(EmailAccount).where(EmailAccount.id != account.id))).scalars().all()
        for other in others:
            other.is_default = False
        account.is_default = True
    elif changes.get("is_default") is False:
        account.is_default = False
    if "display_name" in changes:
        account.display_name = changes["display_name"]
    if "is_active" in changes:
        account.is_active = bool(changes["is_active"])
        if not account.is_active:
            account.is_default = False
    await db.commit()
    return _account_out(account)


@admin_router.delete("/email-accounts/{account_id}", status_code=204)
async def delete_email_account(account_id: int, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    account = await _get_account(db, account_id)
    await db.delete(account)  # tegishli xabarlar email_account_id = NULL bo'ladi (default hisobga o'tadi)
    await db.commit()


@admin_router.post("/email-accounts/{account_id}/test")
async def test_email_account(
    account_id: int, data: TestEmailIn, db: AsyncSession = Depends(get_db), _: User = _admin_dep,
):
    """Hisobdan sinov xati yuboradi (navbatsiz, darhol)."""
    account = await _get_account(db, account_id)
    try:
        provider_id = await gmail.send_mail(account, data.to.strip().lower(), data.subject, data.body)
    except gmail.GmailAuthError as exc:
        account.status = EmailAccountStatus.REAUTH_REQUIRED
        account.last_error = str(exc)[:500]
        await db.commit()
        raise HTTPException(409, str(exc)) from exc
    except (PermanentChannelError, TransientChannelError) as exc:
        raise HTTPException(502, str(exc)) from exc
    await db.commit()
    return {"status": "sent", "provider_message_id": provider_id}


# ---------- Admin: broadcast (barcha foydalanuvchilarga) ----------

@admin_router.post("/broadcast", status_code=202)
async def admin_broadcast(data: BroadcastIn, db: AsyncSession = Depends(get_db), admin: User = _admin_dep):
    """Barcha faol foydalanuvchilarga bir vaqtda: in_app, email, sms yoki telegram kanallari bo'yicha."""
    if not data.template_code and not data.body:
        raise HTTPException(422, "template_code yoki body kerak")
    result = await service.broadcast(
        db,
        channel_list=list(dict.fromkeys(data.channels)),
        template_code=data.template_code,
        subject=data.subject,
        body=data.body,
        html_body=data.html_body,
        link=data.link,
        variables=data.variables,
        category=data.category,
        email_account_id=data.email_account_id,
        created_by=admin.id,
        idempotency_key=data.idempotency_key,
    )
    await db.commit()
    return result


# ---------- Admin: yuborish va navbat ----------

@admin_router.post("/send", status_code=202)
async def admin_send(data: SendIn, db: AsyncSession = Depends(get_db), admin: User = _admin_dep):
    return await _queue(db, data, created_by=admin.id)


@admin_router.get("/messages")
async def list_messages(
    channel: Optional[ChannelT] = None,
    status: Optional[Literal["queued", "sent", "failed", "cancelled"]] = None,
    category: Optional[CategoryT] = None,
    user_id: Optional[uuid.UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = _admin_dep,
):
    stmt = select(NotificationMessage)
    if channel:
        stmt = stmt.where(NotificationMessage.channel == channel)
    if status:
        stmt = stmt.where(NotificationMessage.status == status)
    if category:
        stmt = stmt.where(NotificationMessage.category == category)
    if user_id:
        stmt = stmt.where(NotificationMessage.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(stmt.order_by(NotificationMessage.id.desc()).offset(offset).limit(limit))).scalars().all()
    return {"total": int(total), "items": [_msg_out(m) for m in rows]}


@admin_router.get("/messages/{message_id}")
async def get_message(message_id: int, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    return _msg_out(await _get_message(db, message_id))


@admin_router.post("/messages/{message_id}/retry")
async def retry_message(message_id: int, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    msg = await service.retry_message(db, await _get_message(db, message_id))
    await db.commit()
    return _msg_out(msg)


@admin_router.post("/messages/{message_id}/cancel")
async def cancel_message(message_id: int, db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    msg = await service.cancel_message(db, await _get_message(db, message_id))
    await db.commit()
    return _msg_out(msg)


@admin_router.get("/stats")
async def notification_stats(db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    return await service.stats_by_status(db)


@admin_router.post("/process")
async def process_now(batch: int = Query(50, ge=1, le=500), db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    """Navbatdagi xabarlarni darhol yuborish (worker kutmasdan)."""
    return await service.process_due(db, batch_size=batch)


# ---------- Ichki API (boshqa servislar) ----------

@internal_router.post("/send", status_code=202)
async def internal_send(data: SendIn, db: AsyncSession = Depends(get_db)):
    return await _queue(db, data, created_by=None)


@internal_router.get("/messages/{message_id}")
async def internal_message_status(message_id: int, db: AsyncSession = Depends(get_db)):
    msg = await _get_message(db, message_id)
    return {"id": msg.id, "status": msg.status, "attempts": msg.attempts,
            "last_error": msg.last_error, "sent_at": msg.sent_at}


# ---------- Ochiq: unsubscribe ----------

_UNSUB_PAGE = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Steply</title></head>
<body style="font-family:system-ui,sans-serif;max-width:480px;margin:64px auto;padding:0 16px;color:#111">
<h2>{title}</h2><p>{text}</p></body></html>"""


async def _unsubscribe(token: str, db: AsyncSession) -> HTMLResponse:
    try:
        channel, recipient = service.parse_unsubscribe_token(token)
    except ValueError:
        return HTMLResponse(_UNSUB_PAGE.format(title="Havola yaroqsiz", text="Bu havola noto'g'ri yoki eskirgan."),
                            status_code=400)
    await service.add_optout(db, channel, recipient)
    await db.commit()
    return HTMLResponse(_UNSUB_PAGE.format(
        title="Obuna bekor qilindi",
        text="Siz reklama va yangiliklar xabarlarini olishni to'xtatdingiz. Tranzaksion xabarlar (buyurtma, "
             "ro'yxatdan o'tish) davom etadi.",
    ))


@public_router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_get(token: str = Query(..., min_length=10), db: AsyncSession = Depends(get_db)):
    return await _unsubscribe(token, db)


@public_router.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_post(token: str = Query(..., min_length=10), db: AsyncSession = Depends(get_db)):
    # Gmail/Yahoo "One-Click" unsubscribe (List-Unsubscribe-Post) shu yerga POST yuboradi
    return await _unsubscribe(token, db)


# ---------- Foydalanuvchi: ilova ichidagi bildirishnomalar (inbox) ----------

inbox_router = APIRouter(prefix="/notifications/inbox", tags=["Notification Inbox"])


def _inbox_out(n: UserNotification) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "category": n.category,
        "is_read": n.is_read,
        "created_at": n.created_at,
        "read_at": n.read_at,
    }


async def _unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(await db.scalar(select(func.count(UserNotification.id)).where(
        UserNotification.user_id == user_id, UserNotification.is_read.is_(False),
    )) or 0)


@inbox_router.get("")
async def list_inbox(
    unread_only: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(UserNotification).where(UserNotification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(UserNotification.is_read.is_(False))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(
        stmt.order_by(UserNotification.id.desc()).offset(offset).limit(limit)
    )).scalars().all()
    return {
        "total": int(total),
        "unread": await _unread_count(db, user.id),
        "items": [_inbox_out(n) for n in rows],
    }


@inbox_router.get("/unread-count")
async def inbox_unread_count(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return {"unread": await _unread_count(db, user.id)}


@inbox_router.post("/read-all")
async def inbox_mark_all_read(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(
        update(UserNotification)
        .where(UserNotification.user_id == user.id, UserNotification.is_read.is_(False))
        .values(is_read=True, read_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return {"updated": result.rowcount}


@inbox_router.post("/{item_id}/read")
async def inbox_mark_read(item_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    item = await db.get(UserNotification, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(404, "Bildirishnoma topilmadi")
    if not item.is_read:
        item.is_read = True
        item.read_at = datetime.now(timezone.utc)
    await db.commit()
    return _inbox_out(item)
