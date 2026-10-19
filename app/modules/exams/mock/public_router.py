"""Public, unauthenticated API for mock_center.uz.

Only publishes information that is intentionally visible on public pages.
No user records, answer keys, attempts, or admin-only data are exposed.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from .public_schemas import (
    PublicMockExamDetail, PublicMockExamSummary, PublicSessionDetail, PublicSessionOut,
)

from .models import (
    ExamSession,
    MockExam,
    MockExamAttempt,
    MockExamResult,
    MockRegistration,
    RegistrationStatus,
)


router = APIRouter(prefix="/public", tags=["Public"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    """
    Return current UTC time as a naive datetime.

    SQLite commonly stores SQLAlchemy DateTime values without timezone
    information. Using a naive UTC datetime here keeps comparisons
    consistent with values loaded from the database.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _required_documents(value: str | None) -> list[str]:
    if not value:
        return []

    return [
        item.strip()
        for item in value.split(";")
        if item.strip()
    ]


def _available_seats(
    capacity: int | None,
    registered_count: int,
) -> int | None:
    if capacity is None:
        return None

    return max(capacity - registered_count, 0)


async def _registration_count(
    db: AsyncSession,
    session_id: int,
) -> int:
    result = await db.execute(
        select(func.count(MockRegistration.id)).where(
            MockRegistration.session_id == session_id,
            MockRegistration.status != RegistrationStatus.CANCELLED,
        )
    )

    return int(result.scalar_one() or 0)


def _session_payload(
    session: ExamSession,
    registered_count: int,
) -> dict:
    available = _available_seats(
        session.capacity,
        registered_count,
    )

    return {
        "id": session.id,
        "mock_exam_id": session.mock_exam_id,
        "exam_date": session.exam_date,
        "center_name": session.center_name,
        "room_name": session.room_name,
        "location_address": session.location_address,
        "region": session.region,
        "capacity": session.capacity,
        "registered_count": registered_count,
        "available_seats": available,
        "is_full": (
            available == 0
            if session.capacity is not None
            else False
        ),
        "required_documents": _required_documents(
            session.required_documents
        ),
        "is_active": session.is_active,
    }


# ---------------------------------------------------------------------------
# Public Mock Exams
# ---------------------------------------------------------------------------

@router.get("/mock-exams", response_model=list[PublicMockExamSummary])
async def public_mock_exams(
    db: AsyncSession = Depends(get_db),
    include_past: bool = Query(False),
):
    """
    List active mock exams suitable for the public landing/catalog page.

    By default, only upcoming sessions are returned.
    """

    stmt = (
        select(MockExam)
        .where(MockExam.is_active.is_(True))
        .order_by(MockExam.created_at.desc())
    )

    exams = (
        await db.execute(stmt)
    ).scalars().all()

    now = _utc_now()

    payload = []

    for exam in exams:
        session_stmt = (
            select(ExamSession)
            .where(
                ExamSession.mock_exam_id == exam.id,
                ExamSession.is_active.is_(True),
            )
            .order_by(ExamSession.exam_date.asc())
        )

        sessions = (
            await db.execute(session_stmt)
        ).scalars().all()

        if not include_past:
            sessions = [
                session
                for session in sessions
                if (
                    session.exam_date is None
                    or session.exam_date >= now
                )
            ]

        counts = {}

        for session in sessions:
            counts[session.id] = await _registration_count(
                db,
                session.id,
            )

        payload.append(
            {
                "id": exam.id,
                "title": exam.title,
                "description": exam.description,
                "image_file": exam.image_file,
                "cefr_level": exam.cefr_level,
                "duration_minutes": exam.duration_minutes,
                "price": exam.price,
                "is_active": exam.is_active,
                "upcoming_sessions_count": len(sessions),
                "sessions": [
                    _session_payload(
                        session,
                        counts[session.id],
                    )
                    for session in sessions
                ],
            }
        )

    return payload


# ---------------------------------------------------------------------------
# Public Mock Exam Detail
# ---------------------------------------------------------------------------

@router.get("/mock-exams/{exam_id}", response_model=PublicMockExamDetail)
async def public_mock_exam(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public detail page for one active mock exam.
    """

    exam = await db.get(
        MockExam,
        exam_id,
    )

    if not exam or not exam.is_active:
        raise HTTPException(
            status_code=404,
            detail="Mock imtihon topilmadi",
        )

    now = _utc_now()

    stmt = (
        select(ExamSession)
        .where(
            ExamSession.mock_exam_id == exam.id,
            ExamSession.is_active.is_(True),
            ExamSession.exam_date >= now,
        )
        .order_by(ExamSession.exam_date.asc())
    )

    sessions = (
        await db.execute(stmt)
    ).scalars().all()

    session_payloads = []

    for session in sessions:
        count = await _registration_count(
            db,
            session.id,
        )

        session_payloads.append(
            _session_payload(
                session,
                count,
            )
        )

    return {
        "id": exam.id,
        "title": exam.title,
        "description": exam.description,
        "image_file": exam.image_file,
        "cefr_level": exam.cefr_level,
        "duration_minutes": exam.duration_minutes,
        "price": exam.price,
        "sessions": session_payloads,
    }


# ---------------------------------------------------------------------------
# Public Mock Exam Sessions
# ---------------------------------------------------------------------------

@router.get("/mock-exams/{exam_id}/sessions", response_model=list[PublicSessionOut])
async def public_mock_exam_sessions(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public date/session selector for a particular mock exam.
    """

    exam = await db.get(
        MockExam,
        exam_id,
    )

    if not exam or not exam.is_active:
        raise HTTPException(
            status_code=404,
            detail="Mock imtihon topilmadi",
        )

    now = _utc_now()

    stmt = (
        select(ExamSession)
        .where(
            ExamSession.mock_exam_id == exam_id,
            ExamSession.is_active.is_(True),
            ExamSession.exam_date >= now,
        )
        .order_by(ExamSession.exam_date.asc())
    )

    sessions = (
        await db.execute(stmt)
    ).scalars().all()

    result = []

    for session in sessions:
        count = await _registration_count(
            db,
            session.id,
        )

        result.append(
            _session_payload(
                session,
                count,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Public Sessions
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=list[PublicSessionOut])
async def public_sessions(
    db: AsyncSession = Depends(get_db),
    region: str | None = Query(None),
):
    """
    List all upcoming active sessions for a public sessions page.
    """

    now = _utc_now()

    stmt = (
        select(ExamSession)
        .where(
            ExamSession.is_active.is_(True),
            ExamSession.exam_date >= now,
        )
        .order_by(ExamSession.exam_date.asc())
    )

    if region:
        stmt = stmt.where(
            ExamSession.region == region
        )

    sessions = (
        await db.execute(stmt)
    ).scalars().all()

    result = []

    for session in sessions:
        count = await _registration_count(
            db,
            session.id,
        )

        result.append(
            _session_payload(
                session,
                count,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Public Session Detail
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}", response_model=PublicSessionDetail)
async def public_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(
        ExamSession,
        session_id,
    )

    if not session or not session.is_active:
        raise HTTPException(
            status_code=404,
            detail="Sessiya topilmadi",
        )

    count = await _registration_count(
        db,
        session.id,
    )

    exam = await db.get(
        MockExam,
        session.mock_exam_id,
    )

    if not exam or not exam.is_active:
        raise HTTPException(
            status_code=404,
            detail="Mock imtihon topilmadi",
        )

    return {
        "exam": {
            "id": exam.id,
            "title": exam.title,
            "description": exam.description,
            "cefr_level": exam.cefr_level,
            "duration_minutes": exam.duration_minutes,
            "price": exam.price,
            "image_file": exam.image_file,
        },
        "session": _session_payload(
            session,
            count,
        ),
    }


# ---------------------------------------------------------------------------
# Public Statistics
# ---------------------------------------------------------------------------

@router.get("/stats")
async def public_stats(
    db: AsyncSession = Depends(get_db),
):
    """
    Small aggregate counters for the public homepage.
    """

    now = _utc_now()

    exams = await db.execute(
        select(func.count(MockExam.id)).where(
            MockExam.is_active.is_(True)
        )
    )

    sessions = await db.execute(
        select(func.count(ExamSession.id)).where(
            ExamSession.is_active.is_(True),
            ExamSession.exam_date >= now,
        )
    )

    registrations = await db.execute(
        select(func.count(MockRegistration.id)).where(
            MockRegistration.status
            != RegistrationStatus.CANCELLED
        )
    )

    return {
        "active_mock_exams": int(
            exams.scalar_one() or 0
        ),
        "upcoming_sessions": int(
            sessions.scalar_one() or 0
        ),
        "registrations": int(
            registrations.scalar_one() or 0
        ),
    }


# ---------------------------------------------------------------------------
# Public Result by QR
# ---------------------------------------------------------------------------

@router.get("/results/by-qr/{qr_token}")
async def public_result_by_qr(
    qr_token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public final-result lookup using the private QR token from a permit.

    QR token is intentionally treated as a bearer identifier.
    We never accept a plain Student ID here because Student IDs
    are not secret.
    """

    registration = (
        await db.execute(
            select(MockRegistration).where(
                MockRegistration.qr_token == qr_token
            )
        )
    ).scalar_one_or_none()

    if not registration:
        raise HTTPException(
            status_code=404,
            detail="Natija topilmadi",
        )

    attempt = (
        await db.execute(
            select(MockExamAttempt).where(
                MockExamAttempt.registration_id
                == registration.id
            )
        )
    ).scalar_one_or_none()

    if not attempt:
        raise HTTPException(
            status_code=404,
            detail="Natija hali tayyor emas",
        )

    result = (
        await db.execute(
            select(MockExamResult).where(
                MockExamResult.attempt_id == attempt.id,
                MockExamResult.status == "FINALIZED",
            )
        )
    ).scalar_one_or_none()

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Natija hali tayyor emas",
        )

    return {
        "student_id": registration.student_id,
        "full_name": registration.full_name,
        "result_id": result.id,
        "listening": result.listening_ball,
        "reading": result.reading_ball,
        "writing": result.writing_ball,
        "speaking": result.speaking_ball,
        "overall": result.overall_score,
        "cefr_level": result.cefr_level,
        "status": result.status,
        "finalized_at": result.finalized_at,
    }   