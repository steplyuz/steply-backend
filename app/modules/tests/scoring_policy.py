"""Baholash siyosati — "Chet tilini bilish darajasini baholash ko'p darajali test formati uchun
baholash mezonlari" (Bilimni baholash agentligi, 2023-yil mart) bo'yicha.

1. Tinglab tushunish va o'qish: Rasch qobiliyati θ → Z = (θ − μ)/σ → T = Z·10 + 50 (100 balli).
   Z ≥ 2.5 bo'lsa T = 75 (eng yuqori ball). Z ning yuqori chegarasi C2 ga mos topshiriq yo'qligi uchun.
2. Yozma ish va gapirish: ekspert ballarining raw yig'indisi (0–36) jadval orqali yakuniy ballga (0–75).
   Yozma ish: 1-topshiriq 0–12 (33%), 2-topshiriq 0–24 (67%) — ikkalasining yig'indisi.
3. Umumiy natija: 4 bo'lim (0–75) o'rtachasi → CEFR: C1 65–75, B2 51–64, B1 38–50, B1 dan quyi 0–37.

Eslatma: Rasch hisobi uchun kalibrlash ma'lumotlari (item qiyinligi, μ, σ) kerak. Ular hozircha
repoda yo'q; shu sababli Tinglab tushunish/o'qish uchun mavjud raw→ball jadvali (scale_listening_reading)
ishlatiladi. `rasch_t_score` va `rasch_theta_mle` kalibrlash kelganda shu yerda tayyor.
"""
from __future__ import annotations

import bisect
import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

WS_RAW_MAX = 36           # yozma ish / gapirish: umumiy raw maksimum
WRITING_PART_MAX = {1: 12, 2: 24}
SCALE_MAX = 75
SCALE_MIN = 0
RASCH_CAP_Z = 2.5         # Z >= 2.5 → 75 ball

# Yakuniy ball jadvali (hujjatdan). Raw ball 0.1 aniqlikda; qiymatlar o'0.1 birligida (×10):
# (quyi, yuqori, yakuniy_ball). Qamrov uzluksiz: har bir 0.1 qadam aniq bitta qatorga tushadi.
_RAW_TO_FINAL: list[tuple[int, int, int]] = [
    (351, 360, 75), (341, 350, 74), (331, 340, 73), (326, 330, 72), (321, 325, 71),
    (316, 320, 70), (311, 315, 69), (306, 310, 68), (301, 305, 67), (291, 300, 66),
    (281, 290, 65), (271, 280, 64), (266, 270, 63), (261, 265, 62), (256, 260, 61),
    (251, 255, 60), (246, 250, 59), (241, 245, 58), (236, 240, 57), (231, 235, 56),
    (226, 230, 55), (221, 225, 54), (216, 220, 53), (211, 215, 52), (206, 210, 51),
    (201, 205, 50), (196, 200, 49), (191, 195, 48), (186, 190, 47), (181, 185, 46),
    (176, 180, 45), (171, 175, 44), (166, 170, 43), (161, 165, 42), (156, 160, 41),
    (151, 155, 40), (146, 150, 39), (141, 145, 38), (136, 140, 37), (131, 135, 36),
    (126, 130, 35), (121, 125, 34), (116, 120, 33), (111, 115, 32), (106, 110, 31),
    (101, 105, 30), (96, 100, 29), (91, 95, 28), (86, 90, 27), (81, 85, 26),
    (76, 80, 25), (71, 75, 24), (66, 70, 23), (61, 65, 22), (56, 60, 21),
    (51, 55, 20), (46, 50, 19), (41, 45, 18), (36, 40, 17), (31, 35, 16),
    (26, 30, 15), (21, 25, 14), (16, 20, 13), (11, 15, 12), (6, 10, 11),
    (1, 5, 10), (0, 0, 0),
]
_LOWS = [row[0] for row in sorted(_RAW_TO_FINAL)]
_ROWS_SORTED = sorted(_RAW_TO_FINAL)


def cefr_for_score(score: float) -> str:
    """0–75 ball → CEFR darajasi (yagona manba: C1 65+, B2 51+, B1 38+, aks holda 'B1 dan quyi')."""
    if score >= 65:
        return "C1"
    if score >= 51:
        return "B2"
    if score >= 38:
        return "B1"
    return "B1 dan quyi"


def scale_listening_reading(raw: int, total: int = 35) -> tuple[float, str]:
    """Listening/Reading raw (to'g'ri javoblar soni) → (0–75 ball, CEFR). Yagona manba.

    35 savol uchun hujjat jadvali (0–9 → 0–37, 10–17 → 38–50, 18–27 → 51–64, 28–35 → 65–75).
    Boshqa `total` uchun oddiy proporsiya.
    """
    raw = max(0, min(int(raw), total))
    if total != 35:
        score = round(raw / max(total, 1) * SCALE_MAX, 1)
        return score, cefr_for_score(score)
    if raw >= 28:
        score = 65 + (raw - 28) * (10 / 7)
    elif raw >= 18:
        score = 51 + (raw - 18) * (13 / 9)
    elif raw >= 10:
        score = 38 + (raw - 10) * (12 / 7)
    else:
        score = (raw / 9) * 37
    score = round(min(score, SCALE_MAX), 1)
    return score, cefr_for_score(score)


def _round_half_up(value: float, places: int = 0) -> float:
    quant = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def task_max_raw(part_number: int) -> float:
    """Yozma ish topshirig'ining maksimal raw bali (1-topshiriq 12, 2-topshiriq 24)."""
    if part_number not in WRITING_PART_MAX:
        raise ValueError("Yozma ish topshirig'i qismi noma'lum (1 yoki 2 bo'lishi kerak)")
    return float(WRITING_PART_MAX[part_number])


def writing_speaking_final(raw: float) -> int:
    """Raw yig'indi (0–36) → yakuniy ball (0–75) jadval bo'yicha."""
    if raw is None or raw < 0 or raw > WS_RAW_MAX + 1e-9:
        raise ValueError("raw ball 0–36 oralig'ida bo'lishi kerak")
    tenths = int(_round_half_up(float(raw) * 10, 0))
    idx = bisect.bisect_right(_LOWS, tenths) - 1
    lo, hi, grade = _ROWS_SORTED[idx]
    if not (lo <= tenths <= hi):  # jadval uzluksiz, bu holat bo'lmasligi kerak
        raise ValueError("raw ball jadvalga mos kelmadi")
    return grade


def aggregate_writing(parts: Iterable[tuple[int, float | None]]) -> tuple[float | None, int | None]:
    """(qism_raqami, raw) juftliklaridan yozma ish natijasi.

    Qaytaradi: (raw_yig'indi, yakuniy_ball). Ikkala topshiriq ham baholanmagan bo'lsa yakuniy None.
    Yig'indi har doim qismlar maksimumidan oshmasligi kerak.
    """
    total = 0.0
    seen: set[int] = set()
    for part, raw in parts:
        if raw is None:
            continue
        maximum = task_max_raw(part)
        if raw < 0 or raw > maximum:
            raise ValueError(f"{part}-topshiriq bali 0–{int(maximum)} oralig'ida bo'lishi kerak")
        seen.add(part)
        total += float(raw)
    if seen != set(WRITING_PART_MAX):
        return (round(total, 2) if seen else None), None
    return round(total, 2), writing_speaking_final(total)


def rasch_t_score(theta: float, mu: float, sigma: float) -> int:
    """Rasch qobiliyati → 100 balli standart ball (kalibrlash ma'lumotlari kelganda ishlatiladi)."""
    if sigma <= 0:
        raise ValueError("standart tafovut (σ) musbat bo'lishi kerak")
    z = (theta - mu) / sigma
    if z >= RASCH_CAP_Z:
        return SCALE_MAX
    t = _round_half_up(z * 10 + 50, 0)
    return int(min(SCALE_MAX, max(SCALE_MIN, t)))


def rasch_theta_mle(responses: Sequence[int], difficulties: Sequence[float],
                    bound: float = 6.0, iterations: int = 60) -> float:
    """Rasch modeli bo'yicha qobiliyat (maksimal ehtimollik, Newton-Raphson).

    responses: 1 (to'g'ri) / 0 (noto'g'ri); difficulties: har bir savolning qiyinligi (logit).
    Hamma javob to'g'ri yoki hammasi noto'g'ri bo'lsa MLE mavjud emas — ±bound qaytariladi.
    """
    if len(responses) != len(difficulties) or not responses:
        raise ValueError("javoblar va qiyinliklar soni teng va bo'sh bo'lmasligi kerak")
    if all(r == 1 for r in responses):
        return bound
    if all(r == 0 for r in responses):
        return -bound
    theta = 0.0
    for _ in range(iterations):
        grad = 0.0
        hess = 0.0
        for x, b in zip(responses, difficulties):
            p = 1.0 / (1.0 + math.exp(-(theta - b)))
            grad += x - p
            hess -= p * (1.0 - p)
        if hess == 0:
            break
        step = grad / hess
        theta = max(-bound, min(bound, theta - step))
        if abs(step) < 1e-6:
            break
    return theta


def overall_cefr(sections: Sequence[float]) -> tuple[float, str]:
    """To'rt bo'lim o'rtachasi → (o'rtacha, CEFR). Chegaralar hujjat jadvali bo'yicha."""
    if len(sections) != 4:
        raise ValueError("umumiy natija uchun 4 ta bo'lim balli kerak")
    average = sum(sections) / 4.0
    return average, cefr_for_score(average)
