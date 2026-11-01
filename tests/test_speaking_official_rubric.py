"""Gapirish baholash — rasmiy "Ko'p darajali format" mezoni (reyting shkalasi va qism qoidalari).

- Xom ball 0–21 yig'indisi va reyting shkalasi (reyting_shkala.pdf) bo'yicha yakuniy ball;
- mavzuga mos javoblar soni bo'yicha ball chegaralari (server tomonida cheklash);
- 3-qism: for/against tomonlar qamrovi bo'yicha chegaralar;
- nutq metrikalari (pauza, so'z/daqiqa) audio vaqt belgilaridan.
"""
import asyncio
import json
import os
import tempfile
import types

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_speaking_rubric.db"

import pytest

from app.modules.tests.scoring_policy import SPEAKING_RAW_TO_SCALE, speaking_final
from app.modules.tests.speaking import ai_grader
from app.modules.tests.speaking.scoring import speaking_raw_from_parts, speaking_scaled_from_parts
from app.modules.tests.speaking.transcription import speech_metrics


# ---- reyting shkalasi (hujjatdan, to'liq jadval) ----
OFFICIAL_TABLE = {
    21: 75, 20.5: 73, 20: 71, 19.5: 69, 19: 67, 18.5: 65, 18: 64, 17.5: 63, 17: 61, 16.5: 59,
    16: 57, 15.5: 56, 15: 54, 14.5: 52, 14: 51, 13.5: 50, 13: 49, 12.5: 47, 12: 46, 11.5: 45,
    11: 43, 10.5: 42, 10: 40, 9.5: 39, 9: 38, 8.5: 37, 8: 35, 7.5: 33, 7: 32, 6.5: 30, 6: 29,
    5.5: 27, 5: 26, 4.5: 24, 4: 23, 3.5: 21, 3: 19, 2.5: 17, 2: 15, 1.5: 13, 1: 11, 0.5: 10, 0: 0,
}


def test_conversion_table_matches_official_scale():
    assert SPEAKING_RAW_TO_SCALE == OFFICIAL_TABLE
    for raw, scaled in OFFICIAL_TABLE.items():
        assert speaking_final(raw) == scaled


def test_conversion_rejects_off_grid_and_out_of_range():
    for bad in (21.2, -0.5, 21.5, 22):
        with pytest.raises(ValueError):
            speaking_final(bad)


def test_raw_is_plain_sum_of_parts_and_scales():
    parts = {"1.1": (5, 5), "1.2": (5, 5), "2": (5, 5), "3": (6, 6)}
    assert speaking_raw_from_parts(parts) == 21
    assert speaking_scaled_from_parts(parts) == 75
    parts = {"1.1": (4, 5), "1.2": (4, 5), "2": (3, 5), "3": (3, 6)}
    assert speaking_raw_from_parts(parts) == 14
    assert speaking_scaled_from_parts(parts) == 51


def test_raw_incomplete_or_invalid_part_is_rejected():
    assert speaking_raw_from_parts({"1.1": (5, 5)}) is None
    with pytest.raises(ValueError):
        speaking_raw_from_parts({"1.1": (6, 5), "1.2": (5, 5), "2": (5, 5), "3": (6, 6)})
    with pytest.raises(ValueError):
        speaking_raw_from_parts({"1.1": (5, 5), "1.2": (5, 5), "2": (5, 5), "3": (6, 4)})


@pytest.mark.parametrize("part,on_topic,expected", [
    ("1.1", 0, (0, 0)), ("1.1", 1, (0, 1)), ("1.1", 2, (2, 3)), ("1.1", 3, (2, 5)),
    ("1.2", 1, (0, 1)), ("2", 2, (2, 3)), ("2", 3, (2, 5)),
])
def test_on_topic_caps_for_three_question_parts(part, on_topic, expected):
    assert ai_grader.allowed_range(part, on_topic) == expected


@pytest.mark.parametrize("sides,expected", [(2, (4, 6)), (1, (2, 3)), (0, (0, 2))])
def test_part3_side_caps(sides, expected):
    assert ai_grader.allowed_range("3", 0, sides) == expected


def _fake_client(payload: dict):
    async def create(**_kwargs):
        msg = types.SimpleNamespace(content=json.dumps(payload))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _answers(n: int) -> list[dict]:
    return [{"n": i + 1, "prompt": f"Q{i+1}", "transcript": "I like my friend very much.",
             "metrics": None} for i in range(n)]


def test_part1_score_is_capped_by_on_topic_count(monkeypatch):
    monkeypatch.setattr(ai_grader, "_client", _fake_client({
        "score": 5,
        "questions": [{"n": 1, "on_topic": True}, {"n": 2, "on_topic": False}, {"n": 3, "on_topic": False}],
        "feedback_uz": "test",
    }))
    res = asyncio.run(ai_grader.grade_part("1.1", _answers(3)))
    assert res["score"] == 1
    assert res["raw_model_score"] == 5
    assert res["clamped"] is True


def test_part1_full_on_topic_keeps_model_score(monkeypatch):
    monkeypatch.setattr(ai_grader, "_client", _fake_client({
        "score": 4,
        "questions": [{"n": 1, "on_topic": True}, {"n": 2, "on_topic": True}, {"n": 3, "on_topic": True}],
        "feedback_uz": "test",
    }))
    res = asyncio.run(ai_grader.grade_part("2", _answers(3)))
    assert res["score"] == 4 and res["clamped"] is False


def test_part3_both_sides_required_for_band_four(monkeypatch):
    monkeypatch.setattr(ai_grader, "_client", _fake_client({
        "score": 3,
        "questions": [{"n": 1, "on_topic": True}],
        "sides": {"for": True, "against": True},
        "feedback_uz": "test",
    }))
    res = asyncio.run(ai_grader.grade_part("3", _answers(1), topic_context="Topic: X"))
    assert res["score"] == 4
    assert res["sides"] == {"for": True, "against": True}


def test_part3_one_side_is_capped_at_band_three(monkeypatch):
    monkeypatch.setattr(ai_grader, "_client", _fake_client({
        "score": 5,
        "questions": [{"n": 1, "on_topic": True}],
        "sides": {"for": True, "against": False},
        "feedback_uz": "test",
    }))
    res = asyncio.run(ai_grader.grade_part("3", _answers(1)))
    assert res["score"] == 3


def test_empty_transcripts_score_zero_without_model_call(monkeypatch):
    def boom(**_kwargs):
        raise AssertionError("model chaqirilmasligi kerak")
    monkeypatch.setattr(ai_grader, "_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=boom))))
    answers = [{"n": 1, "prompt": "Q", "transcript": "   ", "metrics": None}]
    res = asyncio.run(ai_grader.grade_part("1.1", answers))
    assert res["score"] == 0


def test_speech_metrics_from_word_timing():
    words = [
        {"w": "I", "s": 0.0, "e": 0.3},
        {"w": "like", "s": 0.4, "e": 0.7},
        {"w": "tea", "s": 3.9, "e": 4.2},   # 3.2s pauza
        {"w": "very", "s": 4.3, "e": 4.6},
        {"w": "much", "s": 8.7, "e": 9.0},  # 4.1s pauza
    ]
    m = speech_metrics(words, duration=10.0)
    assert m["words"] == 5
    assert m["pauses_2s"] == 2
    assert m["longest_pause_s"] == 4.1
    assert m["speech_seconds"] == 9.0
    assert m["wpm"] == round(5 / 9.0 * 60, 1)
    assert speech_metrics([], None)["words"] == 0
