"""Migratsiya zanjiri: bo'sh bazadan `upgrade head` + `alembic check` (CI qabul mezoni)."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": db_url, "AUTO_CREATE_SCHEMA": "false", "ENV": "local"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture(scope="module")
def empty_db_path():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "empty.db"


def test_single_head(empty_db_path):
    r = _alembic("heads", db_url=f"sqlite+aiosqlite:///{empty_db_path}")
    assert r.returncode == 0, r.stderr
    heads = [ln for ln in (r.stdout + r.stderr).splitlines() if "(head)" in ln]
    assert len(heads) == 1, heads


def test_upgrade_head_on_empty_db_then_check_is_clean(empty_db_path):
    url = f"sqlite+aiosqlite:///{empty_db_path}"
    up = _alembic("upgrade", "head", db_url=url)
    assert up.returncode == 0, up.stderr[-2000:]
    chk = _alembic("check", db_url=url)
    assert chk.returncode == 0, (chk.stdout + chk.stderr)[-2000:]
    assert "No new upgrade operations detected" in (chk.stdout + chk.stderr)


def test_seed_defaults_present(empty_db_path):
    url = f"sqlite+aiosqlite:///{empty_db_path}"
    assert _alembic("upgrade", "head", db_url=url).returncode == 0
    con = sqlite3.connect(empty_db_path)
    try:
        sms = con.execute("SELECT value FROM system_settings WHERE key='sms_registration_enabled'").fetchone()
        practice = con.execute("SELECT weekly_limit, monthly_limit FROM tier_practice_limits WHERE tier='FREE'").fetchone()
        analysis = con.execute("SELECT weekly_limit, monthly_limit FROM tier_analysis_limits WHERE tier='FREE'").fetchone()
        dup = con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_%_active_scope_key'"
        ).fetchall()
        legacy = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tier_skill_limits'"
        ).fetchone()
    finally:
        con.close()
    assert sms == ("true",)
    assert practice == (20, None)
    assert analysis == (5, 20)
    assert legacy is None
    assert dup == []  # takroriy indekslar yo'q


def test_postgres_dialect_ddl_generates():
    """Postgres'ga o'tilganda ham zanjir yiqilmasligi: offline SQL generatsiyasi (bazasiz)."""
    r = _alembic("upgrade", "head", "--sql", db_url="postgresql+asyncpg://u:p@localhost/db")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "CREATE TABLE users" in r.stdout
    assert "DEFAULT false" in r.stdout          # sa.false(), '0' emas
    assert "DEFAULT (CURRENT_TIMESTAMP)" not in r.stdout


def test_model_registry_covers_every_table():
    """Ilova import qilgan har bir jadval `models_registry` da bo'lishi shart (aks holda
    baseline/autogenerate uni ko'rmaydi — `org_api_keys` aynan shunday tushib qolgan edi)."""
    code = (
        "import json\n"
        "import app.core.models_registry\n"
        "from app.core.database import Base\n"
        "reg = set(Base.metadata.tables)\n"
        "import app.main\n"
        "full = set(Base.metadata.tables)\n"
        "print(json.dumps(sorted(full - reg)))\n"
    )
    env = {**os.environ, "ENV": "local", "AUTO_CREATE_SCHEMA": "false",
           "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "[]", f"registry'da yo'q jadvallar: {r.stdout}"


@pytest.mark.asyncio
async def test_init_db_rejects_legacy_create_all_database():
    """Alembic'siz (eski create_all) baza taxmin qilinmaydi: aniq xato beradi."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "legacy.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        con.commit()
        con.close()
        code = (
            "import asyncio\n"
            "from app.core.database import init_db, engine\n"
            "async def main():\n"
            "    try:\n"
            "        await init_db()\n"
            "    except RuntimeError as e:\n"
            "        print('RUNTIME:', e)\n"
            "    finally:\n"
            "        await engine.dispose()\n"
            "asyncio.run(main())\n"
        )
        env = {**os.environ, "ENV": "local", "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert "RUNTIME:" in r.stdout and "alembic stamp 0001_baseline" in r.stdout, (r.stdout, r.stderr[-1500:])
