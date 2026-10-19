"""Writing AI javobini o'qish: ortiqcha "{" repair, total_raw_16 va comments shakli."""
import json

from app.modules.tests.writing.services.ai_grader import (
    normalize_rubric_shape,
    parse_model_json,
)

OBJ = {
    "scores": {"coherence": 12, "grammar": 13, "mechanics": 12, "task_achievement": 13, "vocabulary": 13},
    "overall": 14,
    "cefr_level": "B2",
}
PRETTY = json.dumps(OBJ, ensure_ascii=False, indent=2)


def test_valid_json_is_unchanged():
    assert parse_model_json(PRETTY) == OBJ


def test_stray_opening_brace_is_repaired():
    # Log'da ko'rilgan shakl: "{" + newline + "{" ...
    assert parse_model_json("{\n{" + PRETTY[1:]) == OBJ


def test_stray_brace_with_leading_space_is_repaired():
    assert parse_model_json("{\n {" + PRETTY[1:]) == OBJ


def test_nested_object_is_not_stripped():
    assert parse_model_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_garbage_still_raises():
    import pytest
    with pytest.raises(json.JSONDecodeError):
        parse_model_json("hello world")


def test_total_raw_16_moved_out_of_scores():
    raw = {
        "rubric_results": {
            "national": {
                "scores": {"vocabulary": 4, "coherence": 4, "task_fulfilment": 4, "grammar": 4, "total_raw_16": 16},
                "comments": {"vocabulary": "ok"},
            },
        }
    }
    nat = normalize_rubric_shape(raw)["rubric_results"]["national"]
    assert "total_raw_16" not in nat["scores"]
    assert nat["total_raw_16"] == 16


def test_string_comments_are_wrapped():
    raw = {"rubric_results": {"english_band": {"band_score": 5, "comments": "izoh"}}}
    band = normalize_rubric_shape(raw)["rubric_results"]["english_band"]
    assert band["comments"] == {"summary": "izoh"}
