"""Writing / Speaking uchun /lock (answer key yo'q — kontent tuzilmasi muzlatiladi).

Reading/Listening `PaperVersionService` orqali javob kalitini muzlatadi. Writing va
Speaking'da javob kaliti bo'lmagani uchun shu yerda KONTENT strukturasi tekshiriladi va
`ExamVersion` jadvalida (module=WRITING/SPEAKING) lock qilingan snapshot saqlanadi.
Lock qilingach test o'zgartirilmaydi/o'chirilmaydi (409).
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ExamVersion, PaperModule
from .schemas import LockValidationError


class ContentLockService:
    def __init__(self, db: AsyncSession, module: PaperModule, exam_id: str):
        self.db, self.module, self.exam_id = db, module, exam_id

    async def latest(self) -> Optional[ExamVersion]:
        res = await self.db.execute(
            select(ExamVersion)
            .where(ExamVersion.module == self.module, ExamVersion.exam_id == self.exam_id)
            .order_by(desc(ExamVersion.version_number))
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def is_locked(self) -> bool:
        v = await self.latest()
        return bool(v and v.is_locked)

    async def assert_unlocked(self, what: str = "Test") -> None:
        if await self.is_locked():
            raise HTTPException(409, f"{what} locked qilingan. Uni o'zgartirish yoki o'chirish mumkin emas.")

    async def lock(
        self,
        snapshot: Dict[str, Any],
        total: int,
        errors: List[LockValidationError],
        locked_by_user_id=None,
    ) -> Tuple[Optional[ExamVersion], List[LockValidationError]]:
        if errors:
            return None, errors
        latest = await self.latest()
        if latest is not None and not latest.is_locked:
            version = latest
        else:
            version = ExamVersion(
                module=self.module,
                exam_id=self.exam_id,
                version_number=(latest.version_number + 1) if latest else 1,
                is_locked=False,
            )
            self.db.add(version)
        version.total_questions = total
        version.answer_key = snapshot
        version.is_locked = True
        version.locked_at = datetime.now(timezone.utc)
        version.locked_by_user_id = locked_by_user_id
        await self.db.flush()
        return version, []
