"""SUPER_ADMIN rolini berish (faqat server/DB kirish huquqi bo'lgan operator uchun).

SUPER_ADMIN API orqali berilmaydi (admin/router.py himoyasi). Shu sabab bu skript
yagona yo'l. Foydalanuvchi avval oddiy ro'yxatdan o'tgan bo'lishi kerak.

Ishlatish:
    python -m scripts.manage_super_admin +998901234567
    python -m scripts.manage_super_admin someone@example.com
"""
import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User, UserContact, UserRole


async def grant_super_admin(session: AsyncSession, contact_value: str) -> uuid.UUID:
    """`contact_value` (telefon yoki email) bo'yicha foydalanuvchini SUPER_ADMIN qiladi.

    Topilmasa ValueError. Faollashtirilgan user id'sini qaytaradi.
    """
    value = contact_value.strip()
    user = (
        await session.execute(
            select(User)
            .join(UserContact, UserContact.user_id == User.id)
            .where(UserContact.value == value)
            .limit(1)
        )
    ).scalars().first()
    if user is None:
        raise ValueError(f"Kontakt topilmadi: {value}")
    if not user.hashed_password:
        raise ValueError("Bu hisobda parol yo'q (OTP/Telegram orqali kirgan). Avval parol o'rnating.")

    user.global_role = UserRole.SUPER_ADMIN
    user.is_active = True
    await session.commit()
    return user.id


async def _main(contact: str) -> int:
    async with AsyncSessionLocal() as session:
        try:
            user_id = await grant_super_admin(session, contact)
        except ValueError as exc:
            print(f"XATO: {exc}", file=sys.stderr)
            return 1
    print(f"OK: {user_id} endi SUPER_ADMIN")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Foydalanuvchiga SUPER_ADMIN rolini berish")
    parser.add_argument("contact", help="Telefon yoki email (UserContact.value)")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.contact)))


if __name__ == "__main__":
    main()
