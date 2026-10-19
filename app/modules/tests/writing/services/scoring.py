"""Writing ballari: 2025 ko‘p darajali format.

Topshiriqlar: 1.1 (B1, 0–5), 1.2 (B2, 0–5), 2 (C1, 0–6). Ekspert bahosi yig‘indisi (0–16)
quyidagi jadval bo‘yicha 0–75 yakuniy ballga aylanadi ("multilevel-2025 ... format tafsilotlari").
CEFR darajasi yakuniy balldan aniqlanadi (Multilevel-bm: C1 65–75, B2 51–64, B1 38–50).
"""
import re
from dataclasses import dataclass

MAX_EXPERT_TOTAL = 16.0

EXPERT_TO_SCALED = {
    16: 75, 15.5: 72, 15: 69, 14.5: 67, 14: 65, 13.5: 64, 13: 63, 12.5: 62, 12: 61,
    11.5: 59, 11: 57, 10.5: 55, 10: 53, 9.5: 51, 9: 50, 8.5: 48, 8: 47, 7.5: 45,
    7: 43, 6.5: 41, 6: 40, 5.5: 38, 5: 37, 4.5: 35, 4: 33, 3.5: 31, 3: 28, 2.5: 25,
    2: 21, 1.5: 17, 1: 14, 0.5: 10, 0: 0,
}

_WORD_RE = re.compile(r"\b[\w’‘ʻʼ'-]+\b", re.UNICODE)


def count_words(text: str | None) -> int:
    return len(_WORD_RE.findall(text or ""))


def expert_to_scaled(raw_total: float) -> int:
    """Ekspert yig‘indisi (0–16, 0.5 qadam) → 0–75."""
    value = min(max(float(raw_total), 0.0), MAX_EXPERT_TOTAL)
    key = round(value * 2) / 2
    key = int(key) if float(key).is_integer() else key
    return EXPERT_TO_SCALED[key]


def level_from_scaled(scaled: float | None) -> str | None:
    """B1dan quyi → None (CEFRLevel enumida B1 dan past daraja yo‘q)."""
    if scaled is None:
        return None
    if scaled >= 65:
        return "C1"
    if scaled >= 51:
        return "B2"
    if scaled >= 38:
        return "B1"
    return None


@dataclass(frozen=True)
class TaskSpec:
    key: str            # "1.1", "1.2", "2"
    level: str          # topshiriq maqsadli darajasi
    max_score: int      # 5, 5, 6
    genre: str
    style: str
    target_words: str
    # (so‘z soni chegarasi, shu chegaradan oshmasa maksimal ball) — bahosiz tavsiflardan
    length_caps: tuple[tuple[int, int], ...]

    def length_cap(self, words: int) -> int:
        cap = self.max_score
        for limit, max_allowed in self.length_caps:
            if words <= limit:
                cap = min(cap, max_allowed)
        return cap


_SPEC_1_1 = TaskSpec("1.1", "B1", 5, "short informal email to a friend", "informal", "about 50 words", ((25, 2),))
_SPEC_1_2 = TaskSpec("1.2", "B2", 5, "formal email reply to the sender (manager, service, etc.)", "formal", "120-150 words", ((24, 1), (60, 2)))
_SPEC_2 = TaskSpec("2", "C1", 6, "post/article for an online publication (blog post, forum post, magazine article)", "publicistic / popular-science", "180-200 words", ((37, 1), (90, 2)))


def task_spec(part_number: int, sub_part: int | None) -> TaskSpec:
    if int(part_number) == 1:
        return _SPEC_1_1 if sub_part == 1 else _SPEC_1_2
    if int(part_number) == 2:
        return _SPEC_2
    raise ValueError(f"Noma’lum Writing qismi: {part_number}")


# 2025 multilevel tuzilmasi: (part_number, sub_part). Part 2 sub_part'siz.
REQUIRED_FULL_STRUCTURE = ("1.1", "1.2", "2")


def structure_errors(tasks) -> list[str]:
    """Writing testi tuzilmasini tekshiradi (2025 formati): Part 1 — 1.1 va 1.2, Part 2 — bitta topshiriq.

    `tasks` — part_number va sub_part atributlari bor obyektlar. Noto'g'ri tuzilma
    baholashda noto'g'ri spec (masalan sub_part=None → 1.2) yoki 500 xatosiga olib keladi.
    """
    errors: list[str] = []
    seen: set[str] = set()
    for t in tasks:
        part = int(t.part_number)
        if part == 1:
            if t.sub_part not in (1, 2):
                errors.append(f"Part 1 topshirig‘ida sub_part 1 yoki 2 bo‘lishi shart (hozir: {t.sub_part}).")
                continue
            key = f"1.{t.sub_part}"
        elif part == 2:
            if t.sub_part not in (None, 0):
                errors.append("Part 2 topshirig‘ida sub_part bo‘lmasligi kerak.")
                continue
            key = "2"
        else:
            errors.append(f"Noma’lum Writing qismi: {part}. Faqat 1 va 2 ruxsat etiladi.")
            continue
        if key in seen:
            errors.append(f"Topshiriq {key} ikki marta kiritilgan.")
        seen.add(key)
    return errors


def missing_full_structure(tasks) -> list[str]:
    """To'liq (is_practice=false) test uchun yetishmayotgan topshiriqlar ro'yxati (1.1, 1.2, 2)."""
    present = set()
    for t in tasks:
        if int(t.part_number) == 1 and t.sub_part in (1, 2):
            present.add(f"1.{t.sub_part}")
        elif int(t.part_number) == 2:
            present.add("2")
    return [key for key in REQUIRED_FULL_STRUCTURE if key not in present]


# ----------------------------------------------------------------------
# Format parametrlari (2025 spetsifikatsiyasidan) — avtomatik format va tekshiruv uchun
# ----------------------------------------------------------------------
_TASK_WORDS = {"1.1": (45, 55), "1.2": (120, 150), "2": (180, 200)}
_TASK_STYLE = {"1.1": "INFORMAL", "1.2": "FORMAL", "2": "POST"}


def spec_key(part_number: int, sub_part: int | None) -> str:
    return "2" if int(part_number) == 2 else f"1.{sub_part}"


def expected_format(part_number: int, sub_part: int | None) -> dict:
    """Topshiriq uchun kutilgan format: CEFR, uslub, so‘z chegarasi (2025 PDF bo‘yicha)."""
    key = spec_key(part_number, sub_part)
    spec = task_spec(part_number, sub_part)
    min_words, max_words = _TASK_WORDS[key]
    return {"cefr_level": spec.level, "style": _TASK_STYLE[key],
            "min_words": min_words, "max_words": max_words}


def format_mismatch(part_number: int, sub_part: int | None, cefr_level: str, style: str) -> str | None:
    """Mavjud format topshiriq talabiga mos kelmasa, xato matnini qaytaradi; mos bo‘lsa None."""
    exp = expected_format(part_number, sub_part)
    if cefr_level != exp["cefr_level"] or style != exp["style"]:
        return (f"Part {part_number}{'.' + str(sub_part) if sub_part else ''}: format "
                f"{cefr_level}/{style} bo‘lishi kerak edi — 2025 talabi: "
                f"{exp['cefr_level']}/{exp['style']}.")
    return None
