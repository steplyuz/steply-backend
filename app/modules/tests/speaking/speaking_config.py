"""
Speaking testining vaqt qoidalari — YAGONA haqiqat manbai.

Bu qiymatlar faqat test YARATILGANDA (create_test) qism qatorlariga yoziladi.
Shu sababli har test o'z vaqtlarining snapshot'ini saqlaydi: format keyinroq
o'zgarsa, faqat yangi testlar yangi qiymat oladi, eski natijalar buzilmaydi.

DIQQAT: qiymatlarni real imtihon bilan solishtirib tasdiqlang.
  - Part 1.1 / 1.2 dagi prep_seconds (5 / 10): real imtihonda bor, PDF da yo'q.
  - Part 3 answer_seconds = 120: PDF bo'yicha (sizning eski JSON da 180 edi).
  - Part 2 answer_seconds = 120 — UCHTA savol uchun UMUMIY vaqt.
"""

from typing import Final

SPEAKING_TIMINGS: Final[dict[str, dict[str, int]]] = {
    "part_1_1": {"prep_seconds": 5, "answer_seconds": 30},
    "part_1_2": {"prep_seconds": 10, "describe_answer_seconds": 45, "answer_seconds": 30},
    "part_2": {"prep_seconds": 60, "answer_seconds": 120},
    "part_3": {"prep_seconds": 60, "answer_seconds": 120},
}

# Yozuv davomiyligini serverda tekshirish (finalize paytida).
# Limitdan DURATION_GRACE_SECONDS dan ko'p oshsa yozuv rad etiladi (422).
# O'chirish uchun ENFORCE_RECORDING_LIMIT = False qiling.
ENFORCE_RECORDING_LIMIT: Final = True
DURATION_GRACE_SECONDS: Final = 10