"""2025 multilevel Writing tuzilmasi: topshiriqlar, so'z sanash, ball va lock validatsiyasi."""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/writing_struct.db")
os.environ.setdefault("ENV", "local")

from types import SimpleNamespace as T

from app.modules.tests.writing.services.scoring import (
    EXPERT_TO_SCALED,
    expert_to_scaled,
    level_from_scaled,
    missing_full_structure,
    structure_errors,
    task_spec,
)


def tasks(*pairs):
    return [T(part_number=p, sub_part=s) for p, s in pairs]


def test_official_structure_has_no_errors():
    ts = tasks((1, 1), (1, 2), (2, None))
    assert structure_errors(ts) == []
    assert missing_full_structure(ts) == []


def test_part1_without_sub_part_is_rejected():
    # sub_part=None avval 1.2 spec'iga tushib ketardi — endi xato
    ts = tasks((1, None), (2, None))
    assert any("sub_part 1 yoki 2" in e for e in structure_errors(ts))


def test_missing_1_2_is_reported_for_full_exam():
    ts = tasks((1, 1), (2, None))
    assert missing_full_structure(ts) == ["1.2"]


def test_unknown_part_and_duplicate_are_rejected():
    assert any("Noma’lum" in e for e in structure_errors(tasks((3, None))))
    assert any("ikki marta" in e for e in structure_errors(tasks((1, 1), (1, 1), (2, None))))


def test_part2_must_not_have_sub_part():
    assert structure_errors(tasks((2, 1))) != []


def test_task_specs_match_2025_pdf():
    s11, s12, s2 = task_spec(1, 1), task_spec(1, 2), task_spec(2, None)
    assert (s11.level, s11.max_score, s11.target_words) == ("B1", 5, "about 50 words")
    assert (s12.level, s12.max_score, s12.target_words) == ("B2", 5, "120-150 words")
    assert (s2.level, s2.max_score, s2.target_words) == ("C1", 6, "180-200 words")


def test_expert_total_is_16_and_maps_to_75():
    assert 5 + 5 + 6 == 16
    assert expert_to_scaled(16) == 75
    assert expert_to_scaled(0) == 0


def test_cefr_thresholds():
    assert level_from_scaled(75) == "C1"
    assert level_from_scaled(55) == "B2"
    assert level_from_scaled(40) == "B1"
    assert level_from_scaled(30) is None
    assert EXPERT_TO_SCALED[16] == 75


from app.modules.tests.writing.services.scoring import expected_format, format_mismatch


def test_expected_format_per_task_matches_pdf():
    assert expected_format(1, 1) == {"cefr_level": "B1", "style": "INFORMAL", "min_words": 45, "max_words": 55}
    assert expected_format(1, 2) == {"cefr_level": "B2", "style": "FORMAL", "min_words": 120, "max_words": 150}
    assert expected_format(2, None) == {"cefr_level": "C1", "style": "POST", "min_words": 180, "max_words": 200}


def test_format_mismatch_detects_the_bad_export():
    # Eksportdagi xato: 1.1 va 1.2 bitta B1/FORMAL formatga, 2 esa B1 ga bog'langan
    assert format_mismatch(1, 1, "B1", "FORMAL") is not None
    assert format_mismatch(1, 2, "B1", "FORMAL") is not None
    assert format_mismatch(2, None, "B1", "POST") is not None
    assert format_mismatch(1, 1, "B1", "INFORMAL") is None
    assert format_mismatch(2, None, "C1", "POST") is None
