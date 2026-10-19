"""Writing AI baholash tuzatishlari uchun testlar.

Qamrab olinadi:
- AI javobi minimal (floor) qiymatlar bilan sxemadan o'tadi;
- format xatosidan keyin ikkinchi urinish muvaffaqiyatli bo'lishi mumkin;
- provayder xatosi 503, format xatosi 502 bo'ladi;
- talaba topshiriq matnini nusxa ko'chirsa, aniqlanadi, haqiqiy javob esa aniqlanmaydi.
"""
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_tmp}/test_writing_ai_fixes.db")

import httpx
import pytest
from fastapi import HTTPException
from openai import APITimeoutError

from app.modules.tests.writing.services import ai_grader
from app.modules.tests.writing.services.ai_grader import WritingAIGrader
from app.modules.tests.writing.services.result_service import is_task_copy

TASK_TEXT = (
    "Write a short email to another parent. Say what new books or materials you'd like in the library, "
    "what changes should be made, and when the work should begin.\n"
    "You are a parent whose child studies at a local school. You received an email from the school "
    "librarian: \"We are planning to improve our school library. What new books and materials should we buy? "
    "What other changes should be made to make the library more comfortable for students? "
    "When do you think we should start the work?\""
)

FLOOR_VALID = {
    "scores": {"coherence": 1, "vocabulary": 1, "grammar": 1, "task_achievement": 1},
    "overall": 0,
    "cefr_level": "B1",
    "feedback": "Matn tushunarsiz yoki topshiriq nusxasi.",
    "rubric_results": {
        "national": {
            "scores": {"vocabulary": 1, "coherence": 1, "task_fulfilment": 1, "grammar": 1},
            "comments": {},
        },
        "english_band": {"band_score": 2, "comments": {}},
    },
}

REAL_ANSWER = (
    "Dear colleague, I would like the library to buy modern science fiction books and graphic novels. "
    "Comfortable chairs and better lighting would also help students study every afternoon."
)


def _grader_with(side_effects):
    grader = WritingAIGrader.__new__(WritingAIGrader)
    grader.model = "test-model"
    grader.client = MagicMock()
    grader.client.chat.completions.create = AsyncMock(side_effect=side_effects)
    return grader


def _resp(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_floor_values_pass_schema():
    out = ai_grader.validate_evaluation(FLOOR_VALID, 12)
    assert out["overall"] == 0
    assert out["rubric_results"]["english_band"]["band_score"] == 2


def test_task_copy_is_detected():
    assert is_task_copy(TASK_TEXT, TASK_TEXT) is True


def test_partial_task_copy_is_detected():
    partial = TASK_TEXT[:300]
    assert is_task_copy(partial, TASK_TEXT) is True


def test_real_answer_is_not_treated_as_copy():
    assert is_task_copy(REAL_ANSWER, TASK_TEXT) is False


def test_short_text_is_not_treated_as_copy():
    assert is_task_copy("Hi there", TASK_TEXT) is False


def test_random_gibberish_is_not_treated_as_copy():
    gibberish = "dshkvjdsbgjleabgjlbadjlgbdajlfbealjfbjlaebfl ewf weljnflkwenflkdanljfaelkfew peo t ewp"
    assert is_task_copy(gibberish, TASK_TEXT) is False


@pytest.mark.asyncio
async def test_second_attempt_succeeds_after_format_error():
    grader = _grader_with([_resp('{"bad": true}'), _resp(json.dumps(FLOOR_VALID))])
    data = await grader.evaluate(cefr_level="B2", task=TASK_TEXT, answer="a b c d e", max_raw=12)
    assert data["overall"] == 0
    assert grader.client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_retry_message_contains_previous_error():
    grader = _grader_with([_resp('{"bad": true}'), _resp(json.dumps(FLOOR_VALID))])
    await grader.evaluate(cefr_level="B2", task=TASK_TEXT, answer="a b c d e", max_raw=12)
    second_call_messages = grader.client.chat.completions.create.await_args_list[1].kwargs["messages"]
    assert second_call_messages[-1]["role"] == "user"
    assert "previous JSON was rejected" in second_call_messages[-1]["content"]


@pytest.mark.asyncio
async def test_provider_error_returns_503():
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    grader = _grader_with([APITimeoutError(request=req), APITimeoutError(request=req)])
    with pytest.raises(HTTPException) as exc:
        await grader.evaluate(cefr_level="B2", task=TASK_TEXT, answer="a b c d e", max_raw=12)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_format_failure_returns_502():
    grader = _grader_with([_resp("{}"), _resp("{}")])
    with pytest.raises(HTTPException) as exc:
        await grader.evaluate(cefr_level="B2", task=TASK_TEXT, answer="a b c d e", max_raw=12)
    assert exc.value.status_code == 502