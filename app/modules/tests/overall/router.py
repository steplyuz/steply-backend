from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.tests.overall.service import build_overall

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/overall", summary="Barcha skill'lar bo'yicha umumiy practice natijasi")
async def get_overall_result(current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await build_overall(db, current_user.id)
