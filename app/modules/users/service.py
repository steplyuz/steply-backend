from app.core.paths import STATIC_DIR
import os
import uuid
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException

from app.modules.auth.models import (
    User,
    UserProfile,
    UserContact,
    UserSession
)
from app.modules.users import schemas
from app.core.clock import utcnow

# Avatar yuklashda ruxsat etilgan turlar: Content-Type headerga emas, real fayl
# imzosiga (magic bytes) qarab tekshiriladi — mijoz Content-Type'ni soxtalashtirishi
# mumkin (masalan .jpg deb belgilab boshqa turdagi fayl yuborishi).
_AVATAR_MAGIC: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"RIFF", ".webp"),  # WebP: RIFF....WEBP, qo'shimcha tekshiruv pastda
]
_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _avatar_ext_from_magic(data: bytes) -> Optional[str]:
    for sig, ext in _AVATAR_MAGIC:
        if data[: len(sig)] == sig:
            if ext == ".webp" and data[8:12] != b"WEBP":
                continue
            return ext
    return None


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # =====================================================
    # USER
    # =====================================================

    async def get_user_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    # =====================================================
    # PROFILE
    # =====================================================

    async def update_profile(
        self,
        user_id: uuid.UUID,
        data: schemas.ProfileUpdate
    ) -> User:

        result = await self.db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        update_data = data.model_dump(exclude_unset=True)

        if not profile:
            profile = UserProfile(user_id=user_id, **update_data)
            self.db.add(profile)
        else:
            for field, value in update_data.items():
                setattr(profile, field, value)

        if hasattr(profile, "updated_at"):
            profile.updated_at = utcnow()

        await self.db.commit()

        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one()

    # =====================================================
    # CONTACTS
    # =====================================================

    async def get_user_contacts(self, user_id: uuid.UUID) -> List[UserContact]:
        result = await self.db.execute(
            select(UserContact)
            .where(UserContact.user_id == user_id)
            .order_by(UserContact.is_primary.desc())
        )
        return list(result.scalars().all())

    async def add_contact_start(
        self,
        user_id: uuid.UUID,
        value: str,
        contact_type: str
    ) -> dict:

        result = await self.db.execute(
            select(UserContact).where(UserContact.value == value)
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.user_id != user_id:
                raise HTTPException(400, "Bu kontakt boshqa profilga tegishli")

            if existing.is_verified:
                return {
                    "status": "verified",
                    "message": "Kontakt allaqachon tasdiqlangan"
                }

            return {
                "status": "pending_verification",
                "bot_link": self._generate_bot_link()
            }

        contact = UserContact(
            user_id=user_id,
            contact_type=contact_type,
            value=value,
            is_verified=False,
            is_primary=False
        )

        self.db.add(contact)
        await self.db.commit()

        return {
            "status": "pending_verification",
            "bot_link": self._generate_bot_link()
        }

    async def set_primary_contact(
        self,
        user_id: uuid.UUID,
        contact_id: int
    ) -> dict:

        result = await self.db.execute(
            select(UserContact).where(
                UserContact.id == contact_id,
                UserContact.user_id == user_id
            )
        )
        contact = result.scalar_one_or_none()

        if not contact:
            raise HTTPException(404, "Kontakt topilmadi")

        if not contact.is_verified:
            raise HTTPException(400, "Tasdiqlanmagan kontaktni asosiy qilib bo‘lmaydi")

        await self.db.execute(
            update(UserContact)
            .where(
                UserContact.user_id == user_id,
                UserContact.contact_type == contact.contact_type
            )
            .values(is_primary=False)
        )

        contact.is_primary = True

        await self.db.commit()

        return {"message": "Primary kontakt yangilandi"}

    async def delete_contact(
        self,
        user_id: uuid.UUID,
        contact_id: int
    ) -> dict:

        result = await self.db.execute(
            select(UserContact).where(
                UserContact.id == contact_id,
                UserContact.user_id == user_id
            )
        )
        contact = result.scalar_one_or_none()

        if not contact:
            raise HTTPException(404, "Kontakt topilmadi")

        if contact.is_primary:
            raise HTTPException(400, "Asosiy kontaktni o‘chirish mumkin emas")

        await self.db.delete(contact)
        await self.db.commit()

        return {"status": "deleted"}

    # =====================================================
    # AVATAR
    # =====================================================

    async def upload_avatar(
        self,
        user_id: uuid.UUID,
        file: UploadFile
    ) -> str:

        # Hajm chegarasidan 1 bayt ortiqni o'qib ko'ramiz — shu orqali butun
        # faylni xotiraga yuklamasdan turib "juda katta" holatini aniqlaymiz.
        content = await file.read(_AVATAR_MAX_BYTES + 1)
        if not content:
            raise HTTPException(400, "Fayl bo'sh")
        if len(content) > _AVATAR_MAX_BYTES:
            raise HTTPException(413, f"Rasm hajmi {_AVATAR_MAX_BYTES // (1024 * 1024)} MB dan oshmasligi kerak")

        ext = _avatar_ext_from_magic(content)
        if not ext:
            raise HTTPException(415, "Faqat JPG, PNG yoki WebP rasm qabul qilinadi")

        upload_dir = str(STATIC_DIR / "avatars")
        os.makedirs(upload_dir, exist_ok=True)

        filename = f"user_{user_id}_{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(upload_dir, filename)

        try:
            import aiofiles
            async with aiofiles.open(file_path, "wb") as buffer:
                await buffer.write(content)
        except Exception:
            raise HTTPException(500, "Faylni saqlashda xatolik")

        avatar_url = f"/static/avatars/{filename}"

        try:
            result = await self.db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = result.scalar_one_or_none()

            if not profile:
                raise HTTPException(404, "Profil topilmadi")

            old_avatar = profile.avatar_url
            profile.avatar_url = avatar_url

            await self.db.commit()

            if old_avatar and old_avatar.startswith("/static/avatars/"):
                old_name = os.path.basename(old_avatar)
                old_path = os.path.join(str(STATIC_DIR / "avatars"), old_name)
                avatar_root = os.path.abspath(str(STATIC_DIR / "avatars"))
                resolved_old = os.path.abspath(old_path)
                if resolved_old.startswith(avatar_root + os.sep) and os.path.isfile(resolved_old):
                    os.remove(resolved_old)

            return avatar_url

        except Exception:
            await self.db.rollback()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise

    # =====================================================
    # SESSIONS
    # =====================================================

    async def get_active_sessions(
        self,
        user_id: uuid.UUID
    ) -> List[UserSession]:

        result = await self.db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.is_revoked.is_(False)
            )
            .order_by(UserSession.updated_at.desc())
        )

        return list(result.scalars().all())

    async def revoke_session(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID
    ) -> dict:

        result = await self.db.execute(
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id
            )
            .values(is_revoked=True)
        )

        if result.rowcount == 0:
            raise HTTPException(404, "Session topilmadi")

        await self.db.commit()

        return {"status": "revoked"}

    # =====================================================
    # PRIVATE
    # =====================================================

    def _generate_bot_link(self) -> str:
        return "https://t.me/EnwisAuthBot?start=verify_phone"