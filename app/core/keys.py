"""Kriptografik kalitlarni ajratish (domain separation).

Bitta SECRET_KEY'dan turli maqsadlar uchun alohida, mustaqil kalitlar olinadi:
HMAC-SHA256(SECRET_KEY, "steply:v1:<maqsad>"). Shunda bir maqsadda ishlatilgan
kalit (masalan OTP shifrlash) boshqa maqsadda (masalan PKCE) qayta ishlatilmaydi.

JWT imzolash hozircha to'g'ridan-to'g'ri SECRET_KEY bilan qoladi: uni almashtirish
barcha faol access/refresh tokenlarni bekor qiladi, bu alohida deploy qarori.
"""
import base64
import hashlib
import hmac

from app.core.config import settings


def derive_key(purpose: str) -> bytes:
    if not purpose:
        raise ValueError("Kalit maqsadi bo'sh bo'lmasligi kerak")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"steply:v1:{purpose}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def fernet_key(purpose: str) -> bytes:
    """Fernet uchun urlsafe base64 kalit (32 bayt)."""
    return base64.urlsafe_b64encode(derive_key(purpose))
