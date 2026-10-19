"""Steply ro'yxatdan o'tish boti — alohida jarayon.

Ishga tushirish (production):  python bot.py
Bot holatsiz: barcha holat backenddagi VerificationCode yozuvida
(telegram_user_id bo'yicha). Bitta nusxa ishlashi tavsiya etiladi; ikkinchi nusxa
TelegramConflictError oladi va jarayon xato kodi bilan to'xtaydi.
"""
import asyncio
import functools
import ipaddress
import logging
import sys
from urllib.parse import urljoin, urlparse

import httpx
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("SteplyBot")

OK_STATUSES = (200, 201)


class BotSettings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    INTERNAL_API_TOKEN: str
    BACKEND_URL: str = "http://127.0.0.1:8000/api/v1"
    # Default yo'q: qiymat faqat .env dan keladi. Bo'sh yoki ommaviy bo'lmasa,
    # "Saytga qaytish" tugmasi qo'shilmaydi (Telegram localhost'ni rad etadi).
    FRONTEND_URL: str = ""
    TELEGRAM_BILLING_ADMIN_CHAT_ID: str = ""
    TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@functools.lru_cache(maxsize=1)
def get_settings() -> BotSettings:
    return BotSettings()


# ---------------------------------------------------------------- yordamchilar

def is_public_http_url(url: str) -> bool:
    """Telegram inline url tugmasi qabul qiladigan manzilmi?"""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or host == "localhost":
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return "." in host  # domen nomi; "frontend" kabi oddiy hostlar rad etiladi


async def backend(endpoint: str, payload: dict) -> tuple[int, dict | None]:
    """(status, json). status=0 — tarmoq xatosi/timeout."""
    settings = get_settings()
    url = f"{settings.BACKEND_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"X-Internal-Token": settings.INTERNAL_API_TOKEN}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError:
            logger.warning("Backend request failed endpoint=%s", endpoint)
            return 0, None
    try:
        data = response.json() if response.content else None
    except ValueError:
        data = None
    return response.status_code, data


def is_transient(status: int) -> bool:
    return status == 0 or status >= 500


TEMP_ERROR = "⚠️ Vaqtincha xatolik yuz berdi. Iltimos, birozdan so'ng qayta urinib ko'ring."


# ------------------------------------------------------------------ klaviatura

def otp_button() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup.model_validate({
        "inline_keyboard": [[{"text": "📩 Kodni olish", "callback_data": "otp"}]]
    })


def code_button(code: str) -> types.InlineKeyboardMarkup:
    # copy_text: kod callback_data'ga qo'yilmaydi, Telegram uni nusxalab beradi.
    rows: list[list[dict]] = [[{"text": code, "copy_text": {"text": code}}]]
    base = get_settings().FRONTEND_URL.strip()
    if base:
        register_url = urljoin(base.rstrip("/") + "/", "register")
        if is_public_http_url(register_url):
            rows.append([{"text": "🌐 Saytga qaytish", "url": register_url}])
        else:
            logger.warning("FRONTEND_URL ommaviy emas, tugma qo'shilmadi: %s", register_url)
    return types.InlineKeyboardMarkup.model_validate({"inline_keyboard": rows})


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamimni tasdiqlash", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ------------------------------------------------------------------- handlerlar

async def send_code(target: types.Message, telegram_user_id: int) -> str:
    """Kod olib yuboradi. Qaytaradi: 'ok' | 'need_contact' | 'error'."""
    status, data = await backend("auth/telegram/register/code", {"telegram_user_id": telegram_user_id})
    if status in OK_STATUSES and data and data.get("code"):
        await target.answer(
            "🔐 Tasdiqlash kodingiz:\n\n"
            "Kod tugmasini bosing — Telegram uni nusxalab beradi.\n\n"
            "Kodni hech kimga yubormang.",
            reply_markup=code_button(data["code"]),
        )
        return "ok"
    if status == 403:  # telefon hali tasdiqlanmagan
        return "need_contact"
    logger.warning("register/code status=%s", status)
    return "error"


async def cmd_start(message: types.Message) -> None:
    args = (message.text or "").split(maxsplit=1)
    if len(args) != 2 or not args[1]:
        await message.answer("Iltimos, ro'yxatdan o'tish sahifasidan olingan havola orqali botga kiring.")
        return
    tg = message.from_user
    if not tg:
        return

    status, data = await backend("auth/telegram/register/start", {
        "start_token": args[1].strip(),
        "telegram_user_id": tg.id,
        "chat_id": str(message.chat.id),
        "username": tg.username,
        "first_name": tg.first_name,
        "last_name": tg.last_name,
        "photo_url": None,
    })
    if is_transient(status):
        await message.answer(TEMP_ERROR)
        return
    if status not in OK_STATUSES or not data:
        await message.answer("Havola yaroqsiz yoki muddati tugagan. Saytdan yangi havola oling.")
        return

    # Telefon allaqachon tasdiqlangan bo'lsa kod darrov keladi (1 xabar);
    # aks holda bitta xabarda kontakt so'raladi. Oraliq "Kodni olish" tugmasi yo'q.
    outcome = await send_code(message, tg.id)
    if outcome == "need_contact":
        await message.answer(
            "✅ Akkaunt bog'landi. Kodni olish uchun telefon raqamingizni tasdiqlang 👇",
            reply_markup=contact_keyboard(),
        )
    elif outcome == "error":
        await message.answer(TEMP_ERROR, reply_markup=otp_button())


async def get_otp(callback: types.CallbackQuery) -> None:
    await callback.answer()  # spinner darrov to'xtaydi
    if not callback.message:
        return
    outcome = await send_code(callback.message, callback.from_user.id)
    if outcome == "need_contact":
        await callback.message.answer(
            "📱 Kodni olish uchun telefon raqamingizni tasdiqlang 👇",
            reply_markup=contact_keyboard(),
        )
    elif outcome == "error":
        await callback.message.answer(TEMP_ERROR)


async def verify_contact(message: types.Message) -> None:
    tg = message.from_user
    if not tg or not message.contact:
        return
    # Faqat o'z kontaktini yuborish mumkin: user_id None bo'lsa ham rad etiladi.
    if message.contact.user_id != tg.id:
        await message.answer("Iltimos, o'zingizning Telegram akkauntingizdan telefon raqamini yuboring.")
        return

    status, data = await backend("auth/telegram/register/contact", {
        "telegram_user_id": tg.id,
        "chat_id": str(message.chat.id),
        "phone": message.contact.phone_number,
    })
    if is_transient(status):
        await message.answer(TEMP_ERROR, reply_markup=otp_button())
        return
    if status not in OK_STATUSES or not data or not data.get("verified"):
        await message.answer(
            "❌ Telefon raqami mos kelmadi yoki havola eskirdi. "
            "Saytdagi ro'yxatdan o'tish jarayonini qaytadan boshlang.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Alohida "tasdiqlandi" xabari yo'q: kodning o'zi tasdiq. one_time_keyboard
    # klaviaturani o'zi yig'adi. Faqat xatoda zaxira tugma beriladi.
    if await send_code(message, tg.id) != "ok":
        await message.answer(TEMP_ERROR, reply_markup=otp_button())


# ------------------------------------------------------------------------ main

async def billing_review_callback(callback: types.CallbackQuery, bot: Bot) -> None:
    """Admin chatidagi inline tugmalar orqali chekni tasdiqlash/rad etish."""
    settings = get_settings()
    if not settings.TELEGRAM_BILLING_ADMIN_CHAT_ID or str(callback.message.chat.id) != str(settings.TELEGRAM_BILLING_ADMIN_CHAT_ID):
        await callback.answer("Bu amal faqat billing admin chatida ishlaydi", show_alert=True)
        return
    if not settings.TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID or str(callback.from_user.id) != str(settings.TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID):
        await callback.answer("Sizga to‘lovlarni ko‘rib chiqish ruxsati berilmagan", show_alert=True)
        return
    try:
        _, action, order_id = (callback.data or "").split(":", 2)
    except ValueError:
        await callback.answer("Buyurtma ma’lumoti noto‘g‘ri", show_alert=True)
        return
    status, data = await backend("billing/telegram/review", {"order_id": order_id, "action": action})
    if status not in OK_STATUSES or not data:
        message = (data or {}).get("detail") or "Amal bajarilmadi; buyurtma holatini tekshiring"
        await callback.answer(str(message)[:180], show_alert=True)
        return
    await callback.answer("Buyurtma tasdiqlandi" if action == "approve" else "Buyurtma rad etildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Buyurtma {order_id}: {data.get('status', action)}")
    except Exception:
        logger.warning("Could not update billing message after review")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.callback_query.register(get_otp, F.data == "otp")
    dp.callback_query.register(billing_review_callback, F.data.startswith("bill:"))
    dp.message.register(verify_contact, F.contact)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = build_dispatcher()
    logger.info("Steply registration bot started")
    try:
        await dp.start_polling(bot)
    except TelegramConflictError:
        logger.error("Boshqa bot nusxasi getUpdates qilmoqda; jarayon to'xtatildi (supervisor qayta ishga tushiradi)")
        sys.exit(1)
    except Exception:
        logger.exception("Bot polling xatosi bilan to'xtadi")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())