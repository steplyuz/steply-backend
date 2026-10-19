"""
Markazlashtirilgan xatolik (error) formati.

Muammo: ilgari har bir endpoint faqat `detail` matnini qaytarardi
(masalan, "Foydalanuvchi topilmadi"). Frontend faqat shu matnga qarab
qaror qabul qila olmasdi — chunki matn tarjima/o'zgarishi mumkin, va
bir xil matn turlicha vaziyatlarda takrorlanishi mumkin edi.

Yechim: HAR BIR xatolik javobida endi barqaror, mashina o'qiy oladigan
`code` maydoni ham bo'ladi. Kod avtomatik ravishda quyidagilardan
tuziladi:  <ENDPOINT_NOMI>__<HTTP_STATUS>
masalan:  ADMIN_USER_DETAIL__404, LOGIN__401, REGISTER_VERIFY__400

Bu — routerlarning har biriga qo'lda tegmasdan, main.py darajasida,
BARCHA mavjud `HTTPException` chaqiruvlariga avtomatik qo'llanadi.

Frontend uchun javob shakli har doim bir xil:
{
  "error": {
    "status_code": 404,
    "code": "ADMIN_USER_DETAIL__404",
    "message": "Foydalanuvchi topilmadi",
    "fields": null              # faqat validatsiya (422) xatolarida to'ladi
  }
}

Yangi, aniqroq kod kerak bo'lgan joylarda (masalan, bitta endpoint
bir nechta xil sababdan 400 qaytarishi mumkin bo'lsa — "telefon band"
va "kod noto'g'ri" kabi), shu faylda ta'riflangan `AppError`ni
to'g'ridan-to'g'ri ishlatish mumkin:

    from app.core.errors import AppError
    raise AppError(status_code=400, code="AUTH_PHONE_TAKEN", message="Bu telefon raqam allaqachon ro'yxatdan o'tgan")

`AppError` `HTTPException`dan meros oladi, shu sababli uni `except HTTPException`
ushlaydigan joylarda ham muammosiz ishlatish mumkin.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException


class AppError(HTTPException):
    """Barqaror `code` bilan keladigan xatolik. Yangi kod yozishda shundan foydalaning."""

    def __init__(self, status_code: int, code: str, message: str, fields: Optional[dict] = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.fields = fields


_slug_re = re.compile(r"[^A-Z0-9]+")


def make_code(*, route_name: Optional[str], path: str, status_code: int) -> str:
    """Har qanday HTTPException uchun avtomatik, barqaror kod hosil qiladi.

    Router funksiyasining nomi bor bo'lsa o'shani ishlatadi (masalan
    `admin_user_detail`), aks holda URL yo'lidan hosil qiladi.
    """
    base = route_name or path
    slug = _slug_re.sub("_", base.upper()).strip("_") or "ERROR"
    return f"{slug}__{status_code}"
