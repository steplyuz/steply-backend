"""Ochiq (anonim) API javob sxemalari — /public/* endpointlari.

Maydonlar public_router.py dagi payload'lar bilan bir xil. Ichki ma'lumot (javob kalitlari,
urinishlar, foydalanuvchilar) bu yerda yo'q — ular javobga kirmasligi kerak.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PublicSessionOut(BaseModel):
    id: int
    mock_exam_id: str
    exam_date: datetime
    center_name: Optional[str] = None
    room_name: Optional[str] = None
    location_address: str
    region: Optional[str] = None
    capacity: Optional[int] = None
    registered_count: int
    available_seats: Optional[int] = None
    is_full: bool
    required_documents: list[str]
    is_active: bool


class PublicExamInfo(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    cefr_level: Optional[str] = None
    duration_minutes: int
    price: int
    image_file: Optional[str] = None


class PublicMockExamSummary(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    image_file: Optional[str] = None
    cefr_level: Optional[str] = None
    duration_minutes: int
    price: int
    is_active: bool
    upcoming_sessions_count: int
    sessions: list[PublicSessionOut]


class PublicMockExamDetail(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    image_file: Optional[str] = None
    cefr_level: Optional[str] = None
    duration_minutes: int
    price: int
    sessions: list[PublicSessionOut]


class PublicSessionDetail(BaseModel):
    exam: PublicExamInfo
    session: PublicSessionOut
