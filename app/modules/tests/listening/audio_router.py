"""Listening audio — faqat ruxsat etilgan foydalanuvchiga beriladi.

Ilgari audio `/static/listening_audio/` ostida ochiq edi: URL'ni bilgan har kim yuklab olardi.
Endi fayl `storage/private/listening_audio/` da, URL esa shu endpoint orqali. Ruxsat
tekshiruvi test kontenti bilan bir xil (`authorize_test_content`): fayl nomidan shu audioni
ishlatadigan test topiladi va foydalanuvchi o'sha testni ko'ra olishi tekshiriladi.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.file_storage import PRIVATE_ROOT, ensure_private_file
from app.modules.tests.content_access import authorize_test_content, resolve_content_viewer
from app.modules.tests.listening.models import ListeningPart, ListeningTest

router = APIRouter(prefix="/exams", tags=["Listening audio"])

AUDIO_CATEGORY = "listening_audio"
_SAFE_NAME = re.compile(r"^[0-9a-f]{32}\.(mp3|wav|ogg|webm|m4a)$")
_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".webm": "audio/webm", ".m4a": "audio/mp4",
}
_NOT_FOUND = "Audio topilmadi"


@router.get("/listening-audio/{filename}")
async def get_listening_audio(
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not _SAFE_NAME.fullmatch(filename):
        raise HTTPException(404, _NOT_FOUND)

    url = f"/api/v1/exams/listening-audio/{filename}"
    # Bir audio fayl bir nechta testda ishlatilishi mumkin (asl test + part mini-testlar).
    # Foydalanuvchi shulardan KAMIDA BITTASINI ko'ra olsa, audio beriladi.
    tests = list((await db.execute(select(ListeningTest).where(ListeningTest.audio_url == url))).scalars().all())
    exam_ids = (await db.execute(select(ListeningPart.exam_id).where(ListeningPart.audio_url == url))).scalars().all()
    seen = {t.id for t in tests}
    for exam_id in dict.fromkeys(exam_ids):
        if exam_id in seen:
            continue
        test = await db.get(ListeningTest, exam_id)
        if test is not None:
            tests.append(test)
            seen.add(exam_id)
    if not tests:
        raise HTTPException(404, _NOT_FOUND)

    viewer = await resolve_content_viewer(request, db)
    denied: HTTPException | None = None
    for test in tests:
        try:
            await authorize_test_content(db, viewer, test, "LISTENING")
            denied = None
            break
        except HTTPException as exc:
            denied = denied or exc
    if denied is not None:
        raise denied

    path = ensure_private_file(PRIVATE_ROOT / AUDIO_CATEGORY / filename, AUDIO_CATEGORY)
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
