"""Yagona vaqt manbai.

`datetime.utcnow()` Python 3.12 da deprecated. Bu helper xuddi shu qiymatni (naive UTC) beradi,
shuning uchun mavjud ustunlar (SQLite'da timezone'siz saqlanadigan) o'zgarmaydi.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
