"""
migrations/env.py
===================
Bu loyihada oldin `alembic.ini` bor edi (script_location = migrations),
lekin `migrations/` papkasi HECH QACHON yaratilmagan edi — ya'ni Alembic
umuman ishga tushirilmagan (`alembic upgrade head` hech narsa qilmasdi,
chunki bootstrap qilinmagan).

Muhim: ilova asosiy kodda ASYNC drayverlar ishlatadi
(`postgresql+asyncpg://` prod'da, `sqlite+aiosqlite://` dev'da) va
loyihada sync drayver (masalan psycopg2) o'rnatilmagan. Shu sabab bu
yerda Alembic'ning RASMIY async shabloni ishlatiladi (async engine +
`run_sync`) — yangi dependency qo'shmasdan asyncpg/aiosqlite bilan
to'g'ridan-to'g'ri ishlaydi.

`sqlalchemy.url` `alembic.ini`dan EMAS, balki ilovaning
`app.core.config.settings.DATABASE_URL`sidan olinadi.

Baza qarori (MVP): production uchun SQLite (`sqlite+aiosqlite`) — `Settings`
prod rejimida shuni majburlaydi. Migratsiyalar va modellar esa Postgres'da ham
to'g'ri ishlashi uchun dialektga bog'liq bo'lmagan tarzda yoziladi
(`sa.false()`, `sa.func.now()`; SQLite'ga xos DDL yo'q); buni
`tests/test_migrations.py` Postgres dialekti uchun offline SQL generatsiyasi bilan
tekshiradi. Postgres'ga o'tilganda faqat `DATABASE_URL` va prod tekshiruvi o'zgaradi.

Ikki ishga tushirish usuli: CLI (`alembic upgrade head`, o'z event loop'i bilan)
va dastur ichidan (`app.core.database.init_db()` tashqaridan tayyor sync
ulanishni `config.attributes["connection"]` orqali beradi).
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
import sqlalchemy as sa

# Ilova sozlamalari va Base — root papkadan import qilinishi uchun
# alembic.ini'dagi `prepend_sys_path = .` kerak (mavjud).
from app.core.config import settings
from app.core.database import Base

# Barcha model modullari (aks holda autogenerate ularni "yo'q" deb topadi).
import app.core.models_registry  # noqa: F401

# this is the Alembic Config object, which provides access to values within
# the .ini file in use.
config = context.config

# `alembic.ini`dagi statik `sqlalchemy.url`ni ilovaning haqiqiy
# DATABASE_URL bilan almashtiramiz (yuqoridagi izohga qarang).
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    # SQLite stores BillingProductType as VARCHAR while SQLAlchemy's model uses
    # an Enum. The values are intentionally persisted as strings across dialects.
    if (inspected_column.table.name in {"billing_orders", "billing_products"}
            and inspected_column.name == "product_type"
            and isinstance(inspected_type, sa.String)
            and isinstance(metadata_type, sa.Enum)):
        return False
    return None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script generatsiya qilish, DB'ga ulanmasdan)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        compare_type=_compare_type,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=_compare_type,
        # SQLite ALTER cheklangan: kelajakdagi migratsiyalar batch rejimida yoziladi.
        render_as_batch=connection.dialect.name == "sqlite",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Async engine yaratib, uni sync `context.run_migrations()` bilan bog'laydi."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (haqiqiy DB ulanishi bilan)."""
    external = config.attributes.get("connection")
    if external is not None:
        # init_db() ichidan: ishlayotgan event loop ichida asyncio.run() chaqirib bo'lmaydi.
        do_run_migrations(external)
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
