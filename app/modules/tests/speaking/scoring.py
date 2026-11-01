"""Speaking natijasini umumiy shkalaga (0–75) keltirish — rasmiy reyting shkalasi bo'yicha.

Xom ball = to'rt qism ballarining oddiy yig'indisi (0–21). Jadval scoring_policy da (yagona manba).
"""
from app.modules.tests.scoring_policy import SPEAKING_PART_MAX, SPEAKING_RAW_MAX, speaking_final

REQUIRED_PARTS = ("1.1", "1.2", "2", "3")


def speaking_raw_from_parts(parts: dict[str, tuple[int, int]]) -> float | None:
    """parts: {"1.1": (score, max), ...} -> xom ball (0–21). Qism yetishmasa None."""
    if any(code not in parts for code in REQUIRED_PARTS):
        return None
    total = 0
    for code in REQUIRED_PARTS:
        score, maximum = parts[code]
        if score < 0 or score > SPEAKING_PART_MAX[code] or maximum != SPEAKING_PART_MAX[code]:
            raise ValueError(f"{code}-qism bali noto'g'ri: {score}/{maximum}")
        total += score
    return float(min(total, SPEAKING_RAW_MAX))


def speaking_scaled_from_parts(parts: dict[str, tuple[int, int]]) -> float | None:
    raw = speaking_raw_from_parts(parts)
    if raw is None:
        return None
    return float(speaking_final(raw))
