"""Regression tests for Writing timed-out submissions."""

from app.modules.tests.writing.schemas import WritingAnswerSubmit, WritingSubmitRequest


def test_writing_submission_allows_unanswered_task_default():
    payload = WritingSubmitRequest(answers=[{"task_id": 1, "content": ""}])
    assert payload.answers[0].content == ""


def test_writing_submission_trims_and_preserves_partial_answer():
    payload = WritingSubmitRequest(answers=[{"task_id": 1, "content": "  Hi  "}])
    assert payload.answers[0].content == "Hi"


def test_writing_answer_defaults_to_empty_when_content_omitted():
    answer = WritingAnswerSubmit(task_id=2)
    assert answer.content == ""
