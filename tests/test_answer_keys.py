"""Javob kalitlari (content/answer_keys) qog'oz savollar tuzilmasiga mos ekanini tekshiradi.

Qoidalar (LISTENING_PAPER.pdf va READING_PAPER.pdf dan):
- har bir paper 35 savol; har bir raqam kalitda bor;
- harfli savollar variant diapazonida (Listening 1-8: A-C, 15-18: A-F, 19-23: A-H, 24-29: A-C;
  Reading 7-14: A-J, 15-20: A-H, 21-24: A-D, 25-29: A-C (True/False/NI), 34-35: A-D);
- moslashtirish (matching) bo'limlarida har bir harf bir marta ishlatiladi;
- ortiqcha variantlar soni: Listening 3: 2 ta, Listening 4: 3 ta, Reading 2: 2 ta, Reading 3: 2 ta;
- matnli javoblar: alternativlar '/' bilan; har bir alternativ baholashda qabul qilinadi.
"""
import json
import os
import tempfile
from pathlib import Path

# Import oldidan vaqtinchalik baza: app engine sukut bo'yicha loyiha bazasiga ulanib qolmasin
# (aks holda boshqa testlar bilan umumiy ma'lumot holati paydo bo'ladi).
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/keys.db")

import pytest

from app.modules.tests.attempts.common import correct_options, is_correct

KEYS = Path(__file__).resolve().parents[1] / "content" / "answer_keys"
LISTENING = json.loads((KEYS / "listening_key.json").read_text(encoding="utf-8"))["answers"]
READING = json.loads((KEYS / "reading_key.json").read_text(encoding="utf-8"))["answers"]


def _range(start, end):
    return [str(i) for i in range(start, end + 1)]


def test_both_keys_have_35_questions():
    assert sorted(LISTENING, key=int) == _range(1, 35)
    assert sorted(READING, key=int) == _range(1, 35)


@pytest.mark.parametrize("answers,letters,qrange", [
    (LISTENING, "ABC", _range(1, 8)),
    (LISTENING, "ABCDEF", _range(15, 18)),
    (LISTENING, "ABCDEFGH", _range(19, 23)),
    (LISTENING, "ABC", _range(24, 29)),
    (READING, "ABCDEFGHIJ", _range(7, 14)),
    (READING, "ABCDEFGH", _range(15, 20)),
    (READING, "ABCD", _range(21, 24)),
    (READING, "ABC", _range(25, 29)),
    (READING, "ABCD", _range(34, 35)),
])
def test_letter_answers_within_option_range(answers, letters, qrange):
    for q in qrange:
        assert answers[q] in letters, f"savol {q}: {answers[q]!r} variantlar ichida emas"


@pytest.mark.parametrize("answers,qrange,extra", [
    (LISTENING, _range(15, 18), 2),   # F variantdan 4 tasi ishlatiladi
    (LISTENING, _range(19, 23), 3),   # H variantdan 5 tasi ishlatiladi
    (READING, _range(7, 14), 2),      # J variantdan 8 tasi
    (READING, _range(15, 20), 2),     # H headingdan 6 tasi
])
def test_matching_uses_each_option_once_and_extras_are_unused(answers, qrange, extra):
    used = [answers[q] for q in qrange]
    assert len(used) == len(set(used)), "bir variant ikki marta ishlatilgan"
    total_options = {"LISTENING:15-18": 6, "LISTENING:19-23": 8, "READING:7-14": 10, "READING:15-20": 8}
    name = (("LISTENING" if answers is LISTENING else "READING") + f":{qrange[0]}-{qrange[-1]}")
    assert len(used) + extra == total_options[name]


@pytest.mark.parametrize("answers,qs", [
    (LISTENING, _range(9, 14) + _range(30, 35)),
    (READING, _range(1, 6) + _range(30, 33)),
])
def test_text_answers_have_alternatives_that_are_accepted(answers, qs):
    for q in qs:
        opts = correct_options(answers[q])
        assert opts, f"savol {q}: bo'sh kalit"
        for opt in opts:
            assert is_correct([opt], answers[q]), f"savol {q}: '{opt}' qabul qilinmadi"


def test_known_alternatives_and_accents():
    assert is_correct(["seven"], LISTENING["11"]) and is_correct(["7"], LISTENING["11"])
    assert is_correct(["one"], LISTENING["14"]) and is_correct(["1 o’clock"], LISTENING["14"])
    assert is_correct(["Cafe"], LISTENING["10"])     # CAFÉ
    assert not is_correct(["bar"], LISTENING["10"])


def test_keys_only_contain_expected_question_types():
    assert all(v for v in LISTENING.values())
    assert all(v for v in READING.values())
