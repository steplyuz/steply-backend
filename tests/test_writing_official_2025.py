"""Writing — 2025 ko'p darajali format: rasmiy hujjatlar bilan mosligi va AI javob kontrakti.

- Ekspert yig'indisi (0–16) → 0–75 jadvali: hujjatdagi har bir qator;
- so'z soni bo'yicha ball chegaralari (1.1, 1.2, 2);
- 2025 topshiriqlari eski (legacy "overall") kontraktdagi javobni qabul qilmasligi kerak.
"""
import pytest

from app.modules.tests.writing.services import ai_grader
from app.modules.tests.writing.services.scoring import expert_to_scaled, task_spec

# Hujjat: "Ekspert bahosi / Standard ball" jadvali (multilevel 2025 writing)
OFFICIAL_EXPERT_TO_SCALED = {
    17: 75, 16.5: 74, 16: 72, 15.5: 69, 15: 67, 14.5: 65, 14: 63, 13.5: 62, 13: 61,
    12.5: 59, 12: 57, 11.5: 56, 11: 54, 10.5: 53, 10: 51, 9.5: 50, 9: 49, 8.5: 47,
    8: 45, 7.5: 43, 7: 42, 6.5: 40, 6: 38, 5.5: 35, 5: 32, 4.5: 29, 4: 26, 3.5: 23,
    3: 21, 2.5: 18, 2: 15, 1.5: 13, 1: 11, 0.5: 10, 0: 0,
}


@pytest.mark.parametrize("expert,standard", sorted((k, v) for k, v in OFFICIAL_EXPERT_TO_SCALED.items() if k <= 16))
def test_expert_to_scaled_matches_official_table(expert, standard):
    assert expert_to_scaled(expert) == standard


def test_total_is_capped_at_sixteen():
    assert expert_to_scaled(16) == 72
    assert expert_to_scaled(17) == 72


@pytest.mark.parametrize("part,sub,words,cap", [
    # 1.1 (~50 so'z): 50% dan kam (<=25) → A2 (2)
    (1, 1, 25, 2), (1, 1, 26, 5),
    # 1.2 (120–150): A2 — <=24 so'z (1); B1 — 25–60 so'z (2)
    (1, 2, 24, 1), (1, 2, 25, 2), (1, 2, 60, 2), (1, 2, 61, 5),
    # 2 (180–200): A2 — <=37 so'z (1); B1 — 38–90 so'z (2)
    (2, None, 37, 1), (2, None, 38, 2), (2, None, 90, 2), (2, None, 91, 6),
])
def test_length_caps_follow_official_bands(part, sub, words, cap):
    assert task_spec(part, sub).length_cap(words) == cap


def test_2025_task_rejects_legacy_shaped_response():
    legacy = {"overall": 20, "scores": {"grammar": 4}, "cefr_level": "C1", "feedback": "x"}
    with pytest.raises(ValueError):
        ai_grader.validate_evaluation(legacy, task_spec(2, None))


def test_2025_task_accepts_current_contract():
    data = {"score": 5, "feedback": "Yaxshi", "criteria_comments": {}, "errors": [], "corrected_text": ""}
    assert ai_grader.validate_evaluation(data, task_spec(1, 2))["score"] == 5


def test_2025_task_rejects_score_above_task_max():
    data = {"score": 6, "feedback": "x"}
    with pytest.raises(ValueError):
        ai_grader.validate_evaluation(data, task_spec(1, 1))  # 1.1 max = 5
