"""Admin statistika endpointlari: faol vaqt va yechilgan testlar."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.admin.stats_service import ActivityStatsService
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import User, UserRole

router = APIRouter(prefix="/admin/statistics", tags=["Admin Statistics"])
_admin_dep = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))

SORT_PATTERN = (
    "^(total_tests|reading|listening|writing|speaking|"
    "active_seconds|last_seen_at|last_login_at|created_at)$"
)


@router.get("/overview")
async def statistics_overview(db: AsyncSession = Depends(get_db), _: User = _admin_dep):
    return await ActivityStatsService(db).overview()


@router.get("/activity")
async def statistics_activity(
    days: int = Query(30, ge=7, le=180),
    db: AsyncSession = Depends(get_db),
    _: User = _admin_dep,
):
    return {"days": days, "points": await ActivityStatsService(db).activity_by_day(days)}


@router.get("/users")
async def statistics_users(
    sort: str = Query("total_tests", pattern=SORT_PATTERN),
    desc: bool = True,
    search: Optional[str] = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = _admin_dep,
):
    return await ActivityStatsService(db).users_table(
        sort=sort, desc=desc, search=search, offset=offset, limit=limit
    )


@router.get("/users/{user_id}")
async def statistics_user_detail(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = _admin_dep,
):
    data = await ActivityStatsService(db).user_detail(user_id)
    if data is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    return data
