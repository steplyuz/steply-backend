from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.referral import service

router = APIRouter(prefix="/referral", tags=["Referral"])


@router.get("/me")
async def my_referral(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await service.user_summary(db, user.id)
    await db.commit()
    return data
