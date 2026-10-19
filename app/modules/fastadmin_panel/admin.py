"""
FastAdmin panelini Steply loyihasiga ulash.

Muhim: loyihadagi User modelida tayyor `username` / `is_superuser`
ustunlari yo'q (buning o'rniga `UserContact` jadvali va `global_role`
enum ishlatiladi), shuning uchun autentifikatsiya FastAdmin default
naming convention'iga emas, balki quyida qo'lda yozilgan
`authenticate()` metodiga tayanadi.

Bu modul hech qanday yangi DB ulanish yaratmaydi — app.core.database
dagi mavjud AsyncSessionLocal'ni FastAdmin'ga uzatadi.

`UuidPkAdminMixin`: FastAdmin sessiya cookie'dan foydalanuvchi ID'ni
oddiy `str` sifatida qaytaradi va uni to'g'ridan-to'g'ri
`session.get(Model, id)`ga uzatadi. SQLite'da (native UUID yo'q,
CHAR(32) fallback) bu `'str' object has no attribute 'hex'` xatosiga
olib keladi, chunki ustun `uuid.UUID` obyektini kutadi. Shu sabab
UUID PK'li modellar (User) uchun `id`ni har doim
`uuid.UUID`ga aylantirib beramiz — bu Postgres/asyncpg bilan ham
xavfsiz ishlaydi.
"""
from uuid import UUID

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.types import Boolean, Enum as SAEnum, JSON, LargeBinary, String, Text, Uuid as SAUuid
from fastadmin import SqlAlchemyModelAdmin, WidgetType, register

from app.core.database import AsyncSessionLocal, Base
from app.core.security import verify_password
from app.modules.auth.models import User, UserContact, UserRole

# Barcha modul modellarini import qilamiz — shunda Base.registry (pastdagi
# avtomatik registratsiya shu yerdan model ro'yxatini oladi) to'liq bo'ladi.
# Ro'yxat app/core/database.py dagi init_db() bilan bir xil.
from app.modules.exams.mock import models as _mock_models  # noqa: F401
from app.modules.tests.reading import models as _reading_models  # noqa: F401
from app.modules.tests.listening import models as _listening_models  # noqa: F401
from app.modules.tests.writing import models as _writing_models  # noqa: F401
from app.modules.tests.speaking import models as _speaking_models  # noqa: F401
from app.modules.exams.paper import models as _paper_models  # noqa: F401
from app.modules.billing import models as _billing_models  # noqa: F401
from app.modules.exams.auth import models as _exams_auth_models  # noqa: F401


def _coerce_uuid(value):
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return value
    return value


class UuidPkAdminMixin:
    async def orm_get_obj(self, id):
        return await super().orm_get_obj(_coerce_uuid(id))

    async def orm_serialize_obj_by_id(self, id):
        return await super().orm_serialize_obj_by_id(_coerce_uuid(id))

    async def orm_save_obj(self, id, payload):
        return await super().orm_save_obj(_coerce_uuid(id), payload)

    async def orm_delete_obj(self, id):
        return await super().orm_delete_obj(_coerce_uuid(id))


@register(User, sqlalchemy_sessionmaker=AsyncSessionLocal)
class UserAdmin(UuidPkAdminMixin, SqlAlchemyModelAdmin):
    exclude = ("hashed_password",)
    list_display = ("id", "global_role", "is_active", "created_at")
    list_display_links = ("id",)
    list_filter = ("global_role", "is_active")
    search_fields = ()  # UserContact'da qidiruv - pastdagi izohga qarang

    formfield_overrides = {  # noqa: RUF012
        "password": (WidgetType.PasswordInput, {"passwordModalForm": True}),
    }

    async def authenticate(self, username: str, password: str) -> UUID | None:
        """FastAdmin sign-in formasi shu yerga keladi.

        `username` maydoniga email yoki telefon kiritiladi (UserContact.value),
        faqat global_role == SUPER_ADMIN bo'lgan foydalanuvchilarga admin
        panelga kirish beriladi.
        """
        async with AsyncSessionLocal() as session:
            stmt = (
                select(User)
                .join(UserContact, UserContact.user_id == User.id)
                .where(
                    UserContact.value == username,
                    User.global_role == UserRole.SUPER_ADMIN,
                    User.is_active.is_(True),
                )
            )
            user = (await session.execute(stmt)).scalars().first()
            if not user or not user.hashed_password:
                return None
            if not verify_password(password, user.hashed_password):
                return None
            return user.id

    async def change_password(self, id: UUID, password: str) -> None:
        from app.core.security import hash_password

        async with AsyncSessionLocal() as session:
            user = await session.get(User, id)
            if not user:
                return
            user.hashed_password = hash_password(password)
            await session.commit()


# =========================================================================
# Qolgan barcha modellar uchun avtomatik (generik) admin registratsiyasi.
#
# 65+ ta jadval bor, har birini qo'lda yozish o'rniga: ustunlarga qarab
# o'zi list_display/search_fields/list_filter tanlaydi va parol/token/hash
# kabi maxfiy ustunlarni forma va ro'yxatdan yashiradi. Yangi model
# qo'shsangiz ham (init_db() ro'yxatiga qo'shilishi bilanoq) shu yerda
# avtomatik ko'rinadi — qayta yozish shart emas.
# =========================================================================

_SENSITIVE_SUBSTRINGS = ("password", "token", "secret", "hash")
_ALREADY_REGISTERED = {User}


def _is_sensitive_column(name: str) -> bool:
    lname = name.lower()
    return any(s in lname for s in _SENSITIVE_SUBSTRINGS)


def _pk_is_uuid(model) -> bool:
    return any(isinstance(col.type, SAUuid) for col in sa_inspect(model).primary_key)


def _scalar_columns(model):
    """Model ustunlari, katta/og'ir tiplarni (Text/JSON/BLOB) chetlab o'tib."""
    return [
        col
        for col in sa_inspect(model).columns
        if not isinstance(col.type, (Text, JSON, LargeBinary))
    ]


def _default_list_display(model, limit: int = 6) -> tuple[str, ...]:
    pk_names = {c.key for c in sa_inspect(model).primary_key}
    cols = _scalar_columns(model)
    ordered = [c.key for c in cols if c.key in pk_names]
    rest = [c.key for c in cols if c.key not in pk_names and not _is_sensitive_column(c.key)]
    ordered += rest[: max(0, limit - len(ordered))]
    return tuple(ordered) if ordered else ("id",)


def _default_search_fields(model, limit: int = 3) -> tuple[str, ...]:
    out = []
    for col in _scalar_columns(model):
        if _is_sensitive_column(col.key):
            continue
        if isinstance(col.type, String):
            out.append(col.key)
        if len(out) >= limit:
            break
    return tuple(out)


def _default_list_filter(model, limit: int = 4) -> tuple[str, ...]:
    out = []
    for col in _scalar_columns(model):
        if isinstance(col.type, (Boolean, SAEnum)):
            out.append(col.key)
        if len(out) >= limit:
            break
    return tuple(out)


def _sensitive_excludes(model) -> tuple[str, ...]:
    return tuple(c.key for c in sa_inspect(model).columns if _is_sensitive_column(c.key))


def _register_default(model, sessionmaker=AsyncSessionLocal):
    bases = (UuidPkAdminMixin, SqlAlchemyModelAdmin) if _pk_is_uuid(model) else (SqlAlchemyModelAdmin,)
    attrs = {
        "list_display": _default_list_display(model),
        "search_fields": _default_search_fields(model),
    }
    list_filter = _default_list_filter(model)
    if list_filter:
        attrs["list_filter"] = list_filter
    exclude = _sensitive_excludes(model)
    if exclude:
        attrs["exclude"] = exclude
    admin_cls = type(f"{model.__name__}AutoAdmin", bases, attrs)
    register(model, sqlalchemy_sessionmaker=sessionmaker)(admin_cls)
    return admin_cls


# Sessiya, OTP va hash/kalit saqlovchi jadvallar admin paneldan butunlay yashirilgan:
# ularni ko'rish yoki o'zgartirish hisob xavfsizligini buzadi.
_HIDDEN_MODELS = frozenset({
    "UserSession",
    "VerificationCode",
    "OtpSendLimit",
    "Identity",
    "TelegramOIDCTransaction",
    "ExamsVerificationCode",
    "ExamsAuthSession",
})


def _register_all_remaining_models() -> list[str]:
    registered = []
    seen = set()
    for mapper in Base.registry.mappers:
        model = mapper.class_
        if model in seen or model in _ALREADY_REGISTERED or model.__name__ in _HIDDEN_MODELS:
            continue
        seen.add(model)
        _register_default(model)
        registered.append(model.__name__)
    return registered


_auto_registered_models = _register_all_remaining_models()