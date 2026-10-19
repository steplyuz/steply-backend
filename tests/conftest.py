"""Umumiy test sozlamalari.

Production default: MK ID bilan kirish YOPIQ (faqat Exam ID). Eski MK ID yo'li
(ikkinchi omil, throttling, refresh) hali ham sinab turiladi, shuning uchun
testlarda bayroq vaqtincha yoqiladi. Bayroqni o'chirishni tekshiradigan testlar
o'zi False qilib qo'yadi.
"""
import os

# Pytest collects modules in an order that can import app.main before
# tests/test_full_api.py sets its own environment variables. Set the test
# host allowlist here, before any application imports, to avoid 400
# "Invalid host header" responses in the ASGI test client.
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import pytest


@pytest.fixture(autouse=True)
def _enable_legacy_mk_id_login():
    # Lazy import: test modullari DATABASE_URL ni settings import qilinishidan oldin o'rnatadi.
    from app.core.config import settings

    old = settings.EXAMS_MK_ID_LOGIN_ENABLED
    old_before = settings.EXAMS_LOGIN_OPENS_MINUTES_BEFORE
    old_after = settings.EXAMS_LOGIN_CLOSES_MINUTES_AFTER

    settings.EXAMS_MK_ID_LOGIN_ENABLED = True
    settings.EXAMS_LOGIN_OPENS_MINUTES_BEFORE = None
    settings.EXAMS_LOGIN_CLOSES_MINUTES_AFTER = None
    yield
    settings.EXAMS_MK_ID_LOGIN_ENABLED = old
    settings.EXAMS_LOGIN_OPENS_MINUTES_BEFORE = old_before
    settings.EXAMS_LOGIN_CLOSES_MINUTES_AFTER = old_after
