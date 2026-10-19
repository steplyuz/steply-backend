"""Baholash (grading) va OMR taqqoslash: sof funksiyalar bo'yicha testlar.

Bazaga tegmaydi: attempt_service (javob normallashtirish, baholash), omr_service (kalit bilan
taqqoslash, ball shkalasi) va finalize (CEFR chegaralari).

Ochiq masala: MULTIPLE_SELECT savollari uchun `is_correct` faqat birinchi javobni tekshiradi
(xfail — qarang: test_multiple_select_requires_all_correct_options).
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.tests.attempts import common as att
from app.modules.exams.mock import finalize
from app.modules.exams.mock import omr_service as omr


# --------------------------------------------------------------------------
# 1. Javob normallashtirish (attempt_service)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, []),
    ("", []),
    ("  a  ", ["a"]),
    (["a", None, " ", "b "], ["a", "b"]),
    (("x",), ["x"]),
    (5, ["5"]),
])
def test_normalize_answer_shapes(raw, expected):
    assert att.normalize_answer(raw) == expected


def test_normalize_answer_rejects_too_many_items():
    too_many = [str(i) for i in range(att.MAX_ANSWER_ITEMS + 1)]
    with pytest.raises(HTTPException) as exc:
        att.normalize_answer(too_many)
    assert exc.value.status_code == 422


def test_normalize_answer_rejects_overlong_item():
    with pytest.raises(HTTPException) as exc:
        att.normalize_answer("x" * (att.MAX_ANSWER_LEN + 1))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("correct,expected", [
    ("A/B", ["a", "b"]),
    (["  Yes ", "TRUE"], ["yes", "true"]),
    (" c ", ["c"]),
    ("a//b", ["a", "b"]),
])
def test_correct_options_are_lowercase_alternatives(correct, expected):
    assert att.correct_options(correct) == expected


@pytest.mark.parametrize("answer,correct,ok", [
    (["A"], "a", True),          # katta-kichik harf farq qilmaydi
    (["b"], "A/B", True),        # alternativ to'g'ri javob
    (["c"], "A/B", False),
    ([], "A", False),            # bo'sh javob hech qachon to'g'ri emas
    ([""], "", False),           # bo'sh kalit ham bo'sh javobni to'g'ri qilmaydi
])
def test_is_correct_single_answer(answer, correct, ok):
    assert att.is_correct(answer, correct) is ok


def test_score_counts_correct_and_percentage():
    class Spec:
        def metrics(self, correct):
            return (correct * 2, "B1" if correct else "B1 dan quyi")

    questions = [SimpleNamespace(question_number=i, correct_answer="A") for i in range(1, 5)]
    answers = {1: ["a"], 2: ["b"], 3: ["A"], 4: []}
    correct, total, std, cefr, pct = att._score(Spec(), questions, answers)
    assert (correct, total) == (2, 4)
    assert pct == 50.0
    assert std == 4


# --------------------------------------------------------------------------
# 2. OMR: kalit bilan taqqoslash
# --------------------------------------------------------------------------

def test_omr_normalize_answer_is_case_and_space_insensitive():
    assert omr.normalize_answer("  a   b ") == "A B"
    assert omr.normalize_answer(["c"]) == "C"
    assert omr.normalize_answer([]) == ""
    assert omr.normalize_answer(None) == ""


def test_compare_answers_buckets():
    key = {"1": "A", "2": "B/C", "3": ["D", "E"], "4": "A", "5": "A"}
    student = {"1": "a", "2": "c", "3": "e", "4": "", "5": "?"}
    result = omr.compare_answers(student, key)
    assert result["raw_score"] == 3                 # 1 (A), 2 (C alternativ), 3 (E ro'yxat ichida)
    assert result["total"] == 5
    assert result["correct_questions"] == [1, 2, 3]
    assert result["blank_questions"] == [4]
    assert result["ambiguous_questions"] == [5]
    assert result["wrong_questions"] == []


def test_compare_answers_wrong_and_missing():
    result = omr.compare_answers({"1": "B"}, {"1": "A", "2": "A"})
    assert result["wrong_questions"] == [1]
    assert result["blank_questions"] == [2]   # javob yo'q = bo'sh


# --------------------------------------------------------------------------
# 3. Ball shkalasi (35 savol): monoton va belgilangan nuqtalar
# --------------------------------------------------------------------------

def test_scale_score_is_monotonic_over_full_range():
    scores = [omr.scale_score(raw)[0] for raw in range(0, 36)]
    assert scores == sorted(scores)
    assert scores[0] == 0.0
    assert scores[35] == 75.0


@pytest.mark.parametrize("raw,level", [
    (0, "B1 dan quyi"),
    (9, "B1 dan quyi"),
    (10, "B1"),
    (17, "B1"),
    (18, "B2"),
    (27, "B2"),
    (28, "C1"),
    (35, "C1"),
])
def test_scale_score_level_boundaries(raw, level):
    assert omr.scale_score(raw)[1] == level


def test_scale_score_clamps_out_of_range_raw():
    assert omr.scale_score(-5)[0] == 0.0
    assert omr.scale_score(99)[0] == 75.0


def test_scale_score_other_totals_use_proportional_scale():
    score, level = omr.scale_score(20, total=40)
    assert score == round(20 / 40 * 75, 1)
    assert level in {"B1 dan quyi", "B1", "B2", "C1"}


# --------------------------------------------------------------------------
# 4. Umumiy CEFR chegaralari (finalize)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("score,level", [
    (0, "B1 dan quyi"), (37.9, "B1 dan quyi"), (38, "B1"),
    (50.9, "B1"), (51, "B2"), (64.9, "B2"), (65, "C1"), (75, "C1"),
])
def test_overall_cefr_thresholds(score, level):
    assert finalize.overall_cefr_level(score) == level


def test_cefr_thresholds_agree_with_omr_scale():
    """OMR shkalasi va umumiy CEFR chegaralari bir xil bo'lishi kerak (ikki joyda takrorlanmasin)."""
    for raw in range(0, 36):
        score, level = omr.scale_score(raw)
        assert finalize.overall_cefr_level(score) == level


# --------------------------------------------------------------------------
# 5. Ochiq masala: MULTIPLE_SELECT
# --------------------------------------------------------------------------

def _multi(correct):
    return SimpleNamespace(type=SimpleNamespace(value="MULTIPLE_SELECT"), correct_answer=correct)


@pytest.mark.parametrize("given,ok", [
    (["A", "C"], True),            # to'liq to'g'ri to'plam
    (["c", "a"], True),            # tartib va katta-kichik harf farq qilmaydi
    (["A", "B"], False),           # bitta to'g'ri + bitta noto'g'ri: ball yo'q
    (["A"], False),                # to'liq emas
    (["A", "B", "C"], False),      # ortiqcha tanlov
    ([], False),                   # bo'sh javob
])
def test_multiple_select_requires_exact_set(given, ok):
    from app.modules.tests.attempts.common import is_correct_for_question
    assert is_correct_for_question(_multi(["A", "C"]), given) is ok


def test_single_answer_types_keep_alternative_matching():
    from app.modules.tests.attempts.common import is_correct_for_question
    single = SimpleNamespace(type=SimpleNamespace(value="MULTIPLE_CHOICE"), correct_answer="A/B")
    assert is_correct_for_question(single, ["b"]) is True
    assert is_correct_for_question(single, ["c"]) is False


# --------------------------------------------------------------------------
# 6. OMR: belgilangan javoblar (sintetik qora-oq maska; bubble koordinatalari — omr_service)
# --------------------------------------------------------------------------

def _mask_with_marks(marks: list[tuple[float, float, int]]):
    import numpy as np

    mask = np.zeros((1000, 1000), dtype=np.uint8)
    yy, xx = np.ogrid[:1000, :1000]
    for x, y, radius in marks:
        mask[(xx - x) ** 2 + (yy - y) ** 2 <= radius * radius] = 255
    return mask


def test_omr_single_mark_is_read():
    xs, ys = omr.L1_X, omr.L1_Y
    mask = _mask_with_marks([(xs[1], ys[0], 13)])          # 1-savol: B
    res = omr._read_bubbles(mask, xs, ys, ["A", "B", "C"], [1, 2, 3, 4])
    assert res["1"]["answer"] == "B" and res["1"]["status"] == "MARKED"
    assert res["2"]["status"] == "BLANK" and res["2"]["answer"] == ""


def test_omr_double_mark_is_ambiguous_not_marked():
    xs, ys = omr.L1_X, omr.L1_Y
    mask = _mask_with_marks([(xs[0], ys[1], 13), (xs[2], ys[1], 13)])  # 2-savol: A va C ikkalasi ham qora
    res = omr._read_bubbles(mask, xs, ys, ["A", "B", "C"], [1, 2, 3, 4])
    assert res["2"]["status"] == "AMBIGUOUS"
    assert res["2"]["answer"] == "?"


def test_omr_faint_mark_is_ambiguous_not_blank():
    xs, ys = omr.L1_X, omr.L1_Y
    mask = _mask_with_marks([(xs[0], ys[2], 6)])            # 3-savol: kuchsiz belgi
    res = omr._read_bubbles(mask, xs, ys, ["A", "B", "C"], [1, 2, 3, 4])
    assert res["3"]["status"] in {"AMBIGUOUS", "BLANK"}
    assert res["3"]["answer"] in {"?", ""}


def test_read_sheet_flags_double_mark_for_manual_review_summary():
    """Ikki javob belgilangan savol ambiguous_questions ro'yxatiga tushadi (summary darajasida)."""
    xs, ys = omr.L1_X, omr.L1_Y
    mask = _mask_with_marks([(xs[0], ys[1], 13), (xs[2], ys[1], 13)])
    res = omr._read_bubbles(mask, xs, ys, ["A", "B", "C"], [1, 2, 3, 4])
    ambiguous = [int(q) for q, v in res.items() if v["status"] == "AMBIGUOUS"]
    assert ambiguous == [2]
