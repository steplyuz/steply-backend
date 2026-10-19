"""Server-authoritative attempt timer/state helpers.

The browser may display a countdown, but security-sensitive timing decisions
must use these persisted timestamps and the server clock only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def calculate_deadline(started_at: datetime, duration_seconds: int) -> datetime:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    start = as_utc(started_at)
    assert start is not None
    return start + timedelta(seconds=duration_seconds)


def remaining_seconds(expires_at: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if expires_at is None:
        return None
    current = as_utc(now or utc_now())
    deadline = as_utc(expires_at)
    assert current is not None and deadline is not None
    return max(0, int((deadline - current).total_seconds()))


def is_expired(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if expires_at is None:
        return False
    current = as_utc(now or utc_now())
    deadline = as_utc(expires_at)
    assert current is not None and deadline is not None
    return current >= deadline



# Explicit lifecycle transitions for the existing MockExamAttempt state enum.
MOCK_ATTEMPT_TRANSITIONS = {
    "CREATED": {"IN_PROGRESS", "EXPIRED"},
    "IN_PROGRESS": {"EXPIRED", "PAPER_COMPLETED", "SPEAKING_COMPLETED", "READY_FOR_FINAL", "FINALIZED"},
    "PAPER_COMPLETED": {"READY_FOR_FINAL", "FINALIZED", "EXPIRED"},
    "SPEAKING_COMPLETED": {"READY_FOR_FINAL", "FINALIZED", "EXPIRED"},
    "READY_FOR_FINAL": {"FINALIZED", "EXPIRED"},
    "EXPIRED": {"FINALIZED"},
    "FINALIZED": set(),
}


def transition_mock_attempt(attempt, new_status) -> None:
    current = getattr(attempt.status, "value", attempt.status)
    target = getattr(new_status, "value", new_status)
    if current == target:
        return
    if target not in MOCK_ATTEMPT_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Invalid attempt transition: {current} -> {target}")
    attempt.status = new_status

PRACTICE_ATTEMPT_TRANSITIONS = {
    "IN_PROGRESS": {"EXPIRED", "SUBMITTED"},
    "EXPIRED": {"SUBMITTED"},
    "SUBMITTED": set(),
}


def transition_practice_attempt(attempt, new_status) -> None:
    current = getattr(attempt.status, "value", attempt.status)
    target = getattr(new_status, "value", new_status)
    if current == target:
        return
    if target not in PRACTICE_ATTEMPT_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Invalid practice attempt transition: {current} -> {target}")
    attempt.status = new_status
