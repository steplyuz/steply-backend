"""MK ID — imtihon seansiga yozilgandagi unikal talaba kodi.

Format: ``MK-DDMMYY-NNN``
    DDMMYY — tug'ilgan sana (kun/oy/yil)
    NNN    — kriptografik tasodifiy 3 xonali son

Masalan: ``MK-290706-7K4BZQ`` (29.07.2006 da tug'ilgan talaba).

Yangi format (5-bosqich): NNN o'rniga 6 belgili tasodifiy qism, har bir belgi
30 ta belgidan (0/1/I/L/O/U yo'q — o'qishda adashtirilmasin) — sanaga 7.3·10^8
variant. Eski 3 xonali ``MK-DDMMYY-NNN`` va ``MC-...`` ID'lar login'da ishlashda davom etadi.

MUHIM — bu kod FOYDALANUVCHIGA emas, RO'YXATDAN O'TISHGA tegishli
------------------------------------------------------------------
MK ID `mock_registrations.student_id` da, ochiq seansga (ExamSession)
yozilish momentida yaratiladi. Shu sababli:

  * bitta odam turli seanslarga yozilsa, har biri uchun alohida MK ID oladi;
  * MK ID bilan Exams tizimiga kirish AYNAN o'sha seansni bildiradi;
  * imtihonga yozilmagan foydalanuvchida MK ID umuman bo'lmaydi.

FORMAT TARIXI — DIQQAT
-----------------------
Ilgari format ``MC-DDMMYYJXX`` edi (J — jins raqami 5/6, XX — 2 xonali
tasodifiy, jami bitta sanada atigi 100 ta variant). Talab (spec)ga muvofiq
endi ``MK-DDMMYY-NNN``ga o'zgartirildi: prefiks ``MK-``, jins endi kodga
kodlanmaydi, tasodifiy qism 3 xonaga chiqarildi (1000 variant/sana) —
katta sessiyalarda kollisiya ehtimoli pasaydi. Bazadagi ESKI ``MC-...``
qiymatlar o'zgartirilmaydi (ular hali ham `MK_ID_RE` bilan mos kelmaydi,
lekin ular allaqachon yaratilgan yozuvlar — faqat YANGI ID'lar yangi
formatda bo'ladi).
"""

import re
import secrets
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Yangi tasodifiy qism alifbosi (30 belgi). Login'da katta harfga o'tkaziladi.
MK_ID_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_NEW_SUFFIX = r"[2-9A-HJ-KMNP-TV-Z]{6}"
MK_ID_RE = re.compile(rf"^MK-\d{{6}}-(?:\d{{3}}|{_NEW_SUFFIX})$")

# Eski format ("MC-" + 6 xonali sana + 1 xonali jins + 2 xonali tasodifiy).
# Faqat LOGIN/qidiruv paytida qabul qilish uchun — bazada bu formatdagi
# eski ro'yxatlar hali ham bor va ular ishlashda davom etishi kerak
# ("Eski MK ID orqali avvalgi imtihon natijalarini ko'rish imkoniyati
# saqlanadi" — spec talabi). YANGI ID'lar HECH QACHON bu formatda
# yaratilmaydi (qarang: build_mk_id).
LEGACY_MK_ID_RE = re.compile(r"^MC-\d{9}$")

# Login/qidiruv uchun: ikkalasidan biriga mos kelsa yetarli.
ANY_MK_ID_RE = re.compile(rf"^(?:MK-\d{{6}}-(?:\d{{3}}|{_NEW_SUFFIX})|MC-\d{{9}})$")

_MAX_ATTEMPTS = 25


def build_mk_id(birth_date: date) -> str:
    """Formatga mos bitta nomzod kod yasaydi (unikallik tekshirilmaydi)."""
    if birth_date is None:
        raise ValueError("birth_date majburiy")
    suffix = "".join(secrets.choice(MK_ID_ALPHABET) for _ in range(6))
    return f"MK-{birth_date.strftime('%d%m%y')}-{suffix}"


async def generate_unique_mk_id(db: AsyncSession, birth_date: date) -> str:
    """`mock_registrations.student_id` bo'yicha unikal MK ID qaytaradi.

    NNN qismi 1000 ta variant bergani uchun bitta tug'ilgan sanada
    1001-talabada bo'sh joy qolmaydi. Shu holatda 409 emas, tushunarli
    xatolik qaytariladi — chaqiruvchi uni HTTP xatosiga o'giradi.
    """
    from .models import MockRegistration

    for _ in range(_MAX_ATTEMPTS):
        candidate = build_mk_id(birth_date)
        taken = (
            await db.execute(
                select(MockRegistration.id)
                .where(MockRegistration.student_id == candidate)
                .limit(1)
            )
        ).scalar_one_or_none()
        if taken is None:
            return candidate

    raise RuntimeError(
        "MK ID yaratib bo'lmadi: shu tug'ilgan sana uchun barcha "
        "kombinatsiyalar band"
    )
