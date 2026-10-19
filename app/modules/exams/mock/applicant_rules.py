"""Ochiq ro'yxatdan o'tish (apply) uchun normalizatsiya qoidalari.

Region: erkin matn o'rniga Region qiymati saqlanadi. Tanilmagan matn "unknown" bo'ladi
(matn saqlanmaydi — bu hududni ko'rsatuvchi maydon, erkin yozuv emas).
"""
from app.modules.auth.models import Region

ADDRESS_MAX_LENGTH = 120

# Region qiymati → qabul qilinadigan yozuvlar (lotin va o'zbekcha).
_REGION_ALIASES: dict[Region, tuple[str, ...]] = {
    Region.TASHKENT_CITY: ("toshkent shahri", "tashkent city", "tashkent_city", "toshkent sh", "tashkent"),
    Region.TASHKENT_REGION: ("toshkent viloyati", "tashkent region", "tashkent_region"),
    Region.ANDIJAN: ("andijon", "andijan", "andijan viloyati"),
    Region.BUKHARA: ("buxoro", "bukhara", "buxoro viloyati", "bukhara region"),
    Region.FERGANA: ("farg'ona", "fargona", "fergana", "farg'ona viloyati"),
    Region.JIZZAKH: ("jizzax", "jizzakh", "jizzax viloyati"),
    Region.KASHKADARYA: ("qashqadaryo", "kashkadarya", "qarshi"),
    Region.NAVOIY: ("navoiy", "navoi", "navoiy viloyati"),
    Region.NAMANGAN: ("namangan", "namangan viloyati"),
    Region.SAMARKAND: ("samarqand", "samarkand", "samarqand viloyati"),
    Region.SURKHANDARYA: ("surxondaryo", "surkhandarya", "termiz"),
    Region.SYRDARYA: ("sirdaryo", "syrdarya", "guliston"),
    Region.KHOREZM: ("xorazm", "khorezm", "urganch"),
    Region.KARAKALPAKSTAN: ("qoraqalpog'iston", "karakalpakstan", "qoraqalpogiston", "nukus"),
}

_LOOKUP: dict[str, str] = {}
for _region, _names in _REGION_ALIASES.items():
    _LOOKUP[_region.value] = _region.value
    for _name in _names:
        _LOOKUP[_name] = _region.value


def normalize_region(address: str | None) -> str:
    if not address:
        return "unknown"
    cleaned = " ".join(address.strip().lower().replace("‘", "'").replace("’", "'").split())
    if len(cleaned) > ADDRESS_MAX_LENGTH:
        return "unknown"
    return _LOOKUP.get(cleaned, "unknown")


# ─── Telefon / pasport tekshiruvi (ochiq ariza uchun) ────────────────────────
import re as _re

_PASSPORT_RE = _re.compile(r'^(AA|AD)\d{7}$', _re.IGNORECASE)
_PHONE_RE = _re.compile(r'^(\+998|998|0)(9[012345789]|6[125679]|7[01234569])\d{7}$')


def validate_passport(v: str) -> bool:
    return bool(_PASSPORT_RE.match(v.strip()))


def validate_phone(v: str) -> bool:
    cleaned = _re.sub(r'[\s\-\(\)]', '', v)
    return bool(_PHONE_RE.match(cleaned))


def normalize_phone(v: str) -> str:
    c = _re.sub(r'[\s\-\(\)]', '', v)
    if c.startswith('0'):
        c = '+998' + c[1:]
    elif c.startswith('998') and not c.startswith('+'):
        c = '+' + c
    return c
