from datetime import date, datetime, timezone
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.paths import PRIVATE_DIR
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User, UserRole
from app.modules.billing.models import AnalysisUsageLog, AnalysisVideo, SubscriptionTier, TierAnalysisLimit, TierPracticeLimit
from app.modules.billing.entitlements import get_user_tier, _week_start, _month_start, PRODUCT_TIMEZONE

router = APIRouter(prefix="/analysis", tags=["Analysis"])
admin_router = APIRouter(prefix="/admin/analysis", tags=["Admin Analysis"])
VIDEO_DIR = PRIVATE_DIR / "analysis_videos"
MAX_VIDEO_BYTES = 500 * 1024 * 1024
SKILLS = {"READING", "LISTENING", "WRITING", "SPEAKING"}

async def _count(db, user_id, start_date: date) -> int:
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=PRODUCT_TIMEZONE).astimezone(timezone.utc)
    return int((await db.scalar(select(func.count(AnalysisUsageLog.id)).where(AnalysisUsageLog.user_id == user_id, AnalysisUsageLog.consumed_at >= start))) or 0)

@router.get("/quota")
async def analysis_quota(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    tier = await get_user_tier(db, user.id)
    limits = await db.scalar(select(TierAnalysisLimit).where(TierAnalysisLimit.tier == tier))
    wstart, mstart = _week_start(), _month_start()
    used_week, used_month = await _count(db, user.id, wstart), await _count(db, user.id, mstart)
    wl, ml = (limits.weekly_limit, limits.monthly_limit) if limits else (None, None)
    return {"tier": tier.value, "weekly_limit": wl, "weekly_used": used_week, "weekly_remaining": max(0, wl-used_week) if wl is not None else None, "monthly_limit": ml, "monthly_used": used_month, "monthly_remaining": max(0, ml-used_month) if ml is not None else None}

@router.get("/videos")
async def list_available_analysis_videos(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Student catalog of available analysis videos; quota is consumed only on playback."""
    rows = (await db.scalars(
        select(AnalysisVideo).order_by(AnalysisVideo.skill.asc(), AnalysisVideo.test_id.asc())
    )).all()
    return [
        {"id": row.id, "skill": row.skill, "test_id": row.test_id, "title": row.title, "created_at": row.created_at}
        for row in rows
    ]


@router.get("/{skill}/{test_id}")
async def get_analysis(skill: str, test_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    skill = skill.strip().upper()
    if skill not in SKILLS: raise HTTPException(422, "Noto'g'ri skill")
    video = await db.scalar(select(AnalysisVideo).where(AnalysisVideo.skill == skill, AnalysisVideo.test_id == str(test_id)))
    if not video: raise HTTPException(404, "Ushbu test uchun tahlil videosi mavjud emas")
    # Serialize quota checks per user. On PostgreSQL the no-op UPDATE acquires
    # a row lock, preventing concurrent requests from both passing the same
    # last available weekly/monthly unit.
    await db.execute(
        update(User).where(User.id == user.id).values(updated_at=User.updated_at)
    )
    tier = await get_user_tier(db, user.id)
    limits = await db.scalar(select(TierAnalysisLimit).where(TierAnalysisLimit.tier == tier))
    wstart, mstart = _week_start(), _month_start()
    used_week, used_month = await _count(db, user.id, wstart), await _count(db, user.id, mstart)
    if limits and limits.weekly_limit is not None and used_week >= limits.weekly_limit:
        raise HTTPException(429, "Haftalik tahlil limiti tugagan")
    if limits and limits.monthly_limit is not None and used_month >= limits.monthly_limit:
        raise HTTPException(429, "Oylik tahlil limiti tugagan")
    path = Path(video.video_path)
    if not path.is_file(): raise HTTPException(404, "Video fayli serverda topilmadi")
    db.add(AnalysisUsageLog(user_id=user.id, skill=skill, test_id=str(test_id), week_start=wstart))
    # Commit before streaming the file so the user's quota is consumed immediately
    # and the per-user lock is not held for the entire video download.
    await db.commit()
    return FileResponse(path, media_type="video/mp4", filename=path.name)

class LimitUpdate(BaseModel):
    tier: SubscriptionTier
    weekly_limit: int | None = Field(default=None, ge=0)
    monthly_limit: int | None = Field(default=None, ge=0)

async def _set_limit(db, model, tier, weekly, monthly):
    row = await db.scalar(select(model).where(model.tier == tier))
    if row is None:
        row = model(tier=tier, weekly_limit=weekly, monthly_limit=monthly); db.add(row)
    else: row.weekly_limit, row.monthly_limit = weekly, monthly
    await db.flush()
    return {"tier": tier.value, "weekly_limit": row.weekly_limit, "monthly_limit": row.monthly_limit}

@admin_router.get("/statistics")
async def admin_analysis_statistics(_: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    """Admin dashboard uchun practice/analysis sarfi va video sonlari."""
    from app.modules.billing.models import GlobalPracticeUsageLog
    from app.modules.auth.models import User as UserModel
    practice_total = int((await db.scalar(select(func.count(GlobalPracticeUsageLog.id)))) or 0)
    analysis_total = int((await db.scalar(select(func.count(AnalysisUsageLog.id)))) or 0)
    videos_total = int((await db.scalar(select(func.count(AnalysisVideo.id)))) or 0)
    users_total = int((await db.scalar(select(func.count(UserModel.id)))) or 0)
    return {"users_total": users_total, "practice_attempts_total": practice_total, "analysis_requests_total": analysis_total, "analysis_videos_total": videos_total}

@admin_router.get("/limits")
async def get_limits(_: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    result = {}
    for tier in SubscriptionTier:
        p = await db.scalar(select(TierPracticeLimit).where(TierPracticeLimit.tier == tier))
        a = await db.scalar(select(TierAnalysisLimit).where(TierAnalysisLimit.tier == tier))
        result[tier.value] = {"practice": {"weekly_limit": p.weekly_limit if p else None, "monthly_limit": p.monthly_limit if p else None}, "analysis": {"weekly_limit": a.weekly_limit if a else None, "monthly_limit": a.monthly_limit if a else None}}
    return result

@admin_router.put("/limits/practice")
async def set_practice_limit(data: LimitUpdate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    return await _set_limit(db, TierPracticeLimit, data.tier, data.weekly_limit, data.monthly_limit)

@admin_router.put("/limits/analysis")
async def set_analysis_limit(data: LimitUpdate, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    return await _set_limit(db, TierAnalysisLimit, data.tier, data.weekly_limit, data.monthly_limit)

@admin_router.post("/videos")
async def upload_video(skill: str = Form(...), test_id: str = Form(...), title: str = Form(...), file: UploadFile = File(...), admin: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    skill = skill.strip().upper()
    if skill not in SKILLS: raise HTTPException(422, "Noto'g'ri skill")
    ext_by_type = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}
    suffix = ext_by_type.get(file.content_type or "")
    if not suffix: raise HTTPException(415, "Faqat MP4, WebM yoki MOV formatlari qabul qilinadi")
    data = await file.read(MAX_VIDEO_BYTES + 1)
    if not data or len(data) > MAX_VIDEO_BYTES: raise HTTPException(413, "Video bo'sh yoki 500 MB dan katta")
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    dest = VIDEO_DIR / (uuid.uuid4().hex + suffix)
    dest.write_bytes(data)
    row = await db.scalar(select(AnalysisVideo).where(AnalysisVideo.skill == skill, AnalysisVideo.test_id == str(test_id)))
    if row:
        old = Path(row.video_path); row.title, row.video_path, row.created_by = title.strip(), str(dest), admin.id
        if old.is_file(): old.unlink(missing_ok=True)
    else:
        row = AnalysisVideo(skill=skill, test_id=str(test_id), title=title.strip(), video_path=str(dest), created_by=admin.id); db.add(row)
    await db.flush()
    return {"id": row.id, "skill": row.skill, "test_id": row.test_id, "title": row.title}

@admin_router.get("/videos")
async def list_videos(_: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(AnalysisVideo).order_by(AnalysisVideo.created_at.desc()))).all()
    return [{"id": x.id, "skill": x.skill, "test_id": x.test_id, "title": x.title, "created_at": x.created_at} for x in rows]

@admin_router.delete("/videos/{video_id}")
async def delete_video(video_id: int, _: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)), db: AsyncSession = Depends(get_db)):
    row = await db.get(AnalysisVideo, video_id)
    if not row: raise HTTPException(404, "Video topilmadi")
    Path(row.video_path).unlink(missing_ok=True)
    await db.delete(row)
    return {"ok": True}
