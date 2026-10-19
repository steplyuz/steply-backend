"""Telefon raqamini yagona (kanonik) +998XXXXXXXXX formatiga keltirish.

Foydalanuvchi "+998 90 123 45 67", "998901234567" yoki "901234567" kiritishi mumkin;
bazada va taqqoslashda faqat bitta shakl ishlatiladi.
"""
import re

_NON_DIGITS = re.compile(r"\D")


def normalize_uz_phone(raw: str) -> str:
    digits = _NON_DIGITS.sub("", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) != 12 or not digits.startswith("998"):
        raise ValueError("Telefon formati noto'g'ri. Masalan: +998901234567")
    return "+" + digits
