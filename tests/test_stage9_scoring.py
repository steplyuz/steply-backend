"""Baholash siyosati (scoring_policy): "Chet tilini bilish darajasini baholash ko'p darajali test
formati uchun baholash mezonlari" hujjatiga mosligi.

Qamrab olinadi: raw (0–36) → yakuniy (0–75) jadvali (0.1 aniqlik, uzluksiz, monoton, hujjat chegaralari),
yozma ish topshiriqlari (12 + 24) yig'indisi, T = Z·10 + 50 va Z ≥ 2.5 → 75, Rasch θ (MLE), umumiy CEFR.
"""
import pytest

from app.modules.tests import scoring_policy as sp


# --------------------------------------------------------------------------
# 1. Raw → yakuniy ball jadvali
# --------------------------------------------------------------------------

def test_table_covers_every_tenth_without_gaps_and_is_monotonic():
    grades = [sp.writing_speaking_final(t / 10) for t in range(0, 361)]
    assert grades == sorted(grades), "jadval monoton bo'lishi kerak"
    assert grades[0] == 0 and grades[-1] == 75
    assert all(0 <= g <= 75 for g in grades)  # har bir 0.1 qadam aniq bitta yakuniy ballga tushadi


@pytest.mark.parametrize("raw,expected", [
    (0, 0), (0.1, 10), (0.5, 10), (0.6, 11), (1.0, 11), (1.1, 12),
    (16.5, 42), (16.6, 43), (17.0, 43), (17.1, 44),
    (20.1, 50), (20.5, 50), (20.6, 51),
    (28.0, 64), (28.1, 65), (29.0, 65), (29.1, 66), (30.0, 66), (30.1, 67),
    (32.5, 71), (32.6, 72), (33.0, 72), (33.1, 73), (34.0, 73), (34.1, 74), (35.0, 74), (35.1, 75), (36.0, 75),
])
def test_document_boundaries(raw, expected):
    assert sp.writing_speaking_final(raw) == expected


def test_half_up_rounding_between_tenths():
    # 32.55 → 32.6 (yuqoriga yaxlitlanadi) → 72
    assert sp.writing_speaking_final(32.55) == 72


@pytest.mark.parametrize("bad", [-0.1, 36.1, 40])
def test_out_of_range_raw_is_rejected(bad):
    with pytest.raises(ValueError):
        sp.writing_speaking_final(bad)


# --------------------------------------------------------------------------
# 2. Yozma ish: 1-topshiriq 12 ball, 2-topshiriq 24 ball (yig'indi)
# --------------------------------------------------------------------------

def test_task_maximums_follow_document():
    assert sp.task_max_raw(1) == 12
    assert sp.task_max_raw(2) == 24
    with pytest.raises(ValueError):
        sp.task_max_raw(3)


def test_writing_is_sum_not_average_and_converted():
    raw, final = sp.aggregate_writing([(1, 8), (2, 16)])
    assert raw == 24
    assert final == sp.writing_speaking_final(24)  # 57


def test_writing_incomplete_has_no_final_score():
    raw, final = sp.aggregate_writing([(1, 8), (2, None)])
    assert raw == 8 and final is None


def test_writing_part_over_maximum_is_rejected():
    with pytest.raises(ValueError):
        sp.aggregate_writing([(1, 13), (2, 10)])


# --------------------------------------------------------------------------
# 3. Standart ball: T = Z·10 + 50, Z ≥ 2.5 → 75
# --------------------------------------------------------------------------

@pytest.mark.parametrize("theta,expected", [
    (0.0, 50), (1.0, 60), (-1.0, 40), (0.25, 53), (-0.25, 48),
])
def test_t_score_formula(theta, expected):
    assert sp.rasch_t_score(theta, mu=0.0, sigma=1.0) == expected


def test_z_at_or_above_cap_is_75():
    assert sp.rasch_t_score(2.5, 0.0, 1.0) == 75
    assert sp.rasch_t_score(7.0, 0.0, 1.0) == 75


def test_low_end_is_clamped_to_zero():
    assert sp.rasch_t_score(-9.0, 0.0, 1.0) == 0


def test_sigma_must_be_positive():
    with pytest.raises(ValueError):
        sp.rasch_t_score(0.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# 4. Rasch qobiliyati (MLE)
# --------------------------------------------------------------------------

def test_mle_symmetric_items_gives_zero_for_half_correct():
    theta = sp.rasch_theta_mle([1, 1, 0, 0], [-1.0, 0.0, 0.0, 1.0])
    assert abs(theta) < 1e-4


def test_mle_increases_with_more_correct_answers():
    diffs = [-1.0, -0.5, 0.0, 0.5, 1.0]
    low = sp.rasch_theta_mle([1, 0, 0, 0, 0], diffs)
    mid = sp.rasch_theta_mle([1, 1, 0, 0, 0], diffs)
    high = sp.rasch_theta_mle([1, 1, 1, 1, 0], diffs)
    assert low < mid < high


def test_mle_extreme_scores_are_bounded():
    assert sp.rasch_theta_mle([1, 1, 1], [0.0, 0.0, 0.0]) == 6.0
    assert sp.rasch_theta_mle([0, 0, 0], [0.0, 0.0, 0.0]) == -6.0


def test_mle_requires_matching_lengths():
    with pytest.raises(ValueError):
        sp.rasch_theta_mle([1, 0], [0.0])


# --------------------------------------------------------------------------
# 5. Umumiy natija: 4 bo'lim o'rtachasi → CEFR (hujjat chegaralari)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sections,level", [
    ([65, 65, 65, 65], "C1"),
    ([64, 64, 64, 64], "B2"),
    ([51, 51, 51, 51], "B2"),
    ([50, 50, 50, 50], "B1"),
    ([38, 38, 38, 38], "B1"),
    ([37, 37, 37, 37], "B1 dan quyi"),
])
def test_overall_cefr_thresholds(sections, level):
    _, got = sp.overall_cefr(sections)
    assert got == level


def test_overall_cefr_matches_product_mapping():
    """Ikki joyda takrorlanmasin: policy va finalize bir xil natija berishi kerak."""
    from app.modules.exams.mock.finalize import overall_cefr_level

    for avg in [0, 37.9, 38, 50.9, 51, 64.9, 65, 75]:
        _, policy_level = sp.overall_cefr([avg] * 4)
        assert policy_level == overall_cefr_level(avg)


def test_overall_requires_four_sections():
    with pytest.raises(ValueError):
        sp.overall_cefr([50, 50, 50])


def test_single_source_for_listening_reading_scale():
    """Raw→ball formulasi bitta joyda: barcha yo'llar bir xil natija beradi (regressiya: ilgari 4 nusxa)."""
    from app.modules.exams.mock import omr_service, services
    from app.modules.exams.mock.models import SkillType
    from app.modules.tests.reading.services import ReadingService
    from app.modules.tests.listening.services import ListeningService

    for raw in range(0, 36):
        expected = sp.scale_listening_reading(raw)
        assert omr_service.scale_score(raw) == expected
        assert ReadingService(None)._calculate_metrics(raw) == expected
        assert ListeningService(None)._calculate_listening_metrics(raw) == expected
        for skill in (SkillType.READING, SkillType.LISTENING):
            assert services.calculate_scaled_score(raw, skill) == expected[0]
        assert services.get_cefr_level(expected[0]) == expected[1] == sp.cefr_for_score(expected[0])
