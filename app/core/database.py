from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy import event
from app.core.config import settings

DATABASE_URL = settings.DATABASE_URL
engine = create_async_engine(
    DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    # SQLite's busy_timeout PRAGMA handles in-process write contention, while
    # the driver timeout prevents the sqlite connection itself from failing
    # earlier than the configured database wait window.
    connect_args={"timeout": 15.0} if DATABASE_URL.startswith("sqlite") else {},
)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # FK'lar SQLite'da default o'chiq: busiz ON DELETE CASCADE ishlamaydi,
        # bola qatorlar yetim qoladi va id qayta ishlatilganda UNIQUE xato beradi.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")      # o'qish/yozish bir-birini bloklamaydi
        cur.execute("PRAGMA busy_timeout=15000")    # "database is locked" o'rniga kutadi
        cur.close()


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _alembic_config():
    from pathlib import Path
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def _upgrade_to_head(sync_conn) -> None:
    """Alembic `upgrade head` ni tashqaridan berilgan sync ulanish orqali ishga tushiradi."""
    from alembic import command
    from sqlalchemy import inspect

    tables = set(inspect(sync_conn).get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        # Eski `create_all()` bazasi: baseline ustiga yaratib bo'lmaydi va sxema
        # holatini taxmin qilmaymiz.
        raise RuntimeError(
            "Baza Alembic'siz (eski create_all) yaratilgan. Dev bazani o'chirib qayta yarating "
            "yoki sxema joriy bilan bir xilligini tekshirib `alembic stamp 0001_baseline` qiling "
            "(docs/MIGRATIONS.md)."
        )
    cfg = _alembic_config()
    cfg.attributes["connection"] = sync_conn
    command.upgrade(cfg, "head")


async def init_db():
    """Sxemani Alembic orqali `head`ga ko'taradi (AUTO_CREATE_SCHEMA=true, dev/test).

    Sxemaning YAGONA manbai — migratsiyalar. Oldingi `create_all()` va qo'lda
    ALTER TABLE ("mini migration") olib tashlandi: ular Alembic bilan ikkilanardi.
    Productionda bu funksiya chaqirilmaydi; u yerda `alembic upgrade head` ishlatiladi.
    """
    # Barcha model modullari metadata'da bo'lishi uchun import.
    import app.core.models_registry  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(_upgrade_to_head)
