from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.activity.service import ActivityService
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User

router = APIRouter(prefix="/activity", tags=["Activity"])


@router.post("/heartbeat")
async def heartbeat(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ActivityService(db).heartbeat(current_user.id)
    return {"ok": True}
