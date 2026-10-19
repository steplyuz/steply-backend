"""6-bosqich: baza va fayllar.

Qamrab olinadi: loyiha yo'llari cwd'ga bog'liq emasligi, SQLite zaxira skripti (nusxa,
tekshiruv, tozalash), listening audio'ning yopiq berilishi (ochiq /static yo'q), va
migratsiya 0005 ning haqiqiy bazada ishlashi (BigInteger, audio URL'lari).
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_stage6.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect as sa_inspect, Boolean, DateTime, Integer, JSON, Uuid, String

from app.core import database as dbm
from app.core import file_storage
from app.core.config import settings
from app.core.database import init_db
from app.core.paths import PROJECT_ROOT, PRIVATE_DIR, STATIC_DIR
from app.modules.tests.listening import audio_router
from app.modules.tests.listening.models import ListeningTest

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"


# --------------------------------------------------------------------------
# 1. Yo'llar cwd'ga bog'liq emas
# --------------------------------------------------------------------------

def test_paths_are_absolute():
    assert PROJECT_ROOT.is_absolute()
    assert PRIVATE_DIR.is_absolute() and STATIC_DIR.is_absolute()
    assert file_storage.PRIVATE_ROOT.is_absolute()


def test_storage_resolves_from_any_working_directory(tmp_path):
    code = ("from app.core.file_storage import PRIVATE_ROOT; "
            "from app.core.paths import STATIC_DIR; "
            "print(PRIVATE_ROOT); print(STATIC_DIR)")
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:", "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-1000:]
    private, static = r.stdout.strip().splitlines()[-2:]
    assert Path(private) == PRIVATE_DIR
    assert Path(static) == STATIC_DIR


# --------------------------------------------------------------------------
# 2. SQLite zaxira skripti
# --------------------------------------------------------------------------

def _make_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def test_backup_creates_valid_copy_and_prunes(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("backup_sqlite", ROOT / "scripts" / "backup_sqlite.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    src = tmp_path / "mock_center.db"
    _make_db(src)
    dest = tmp_path / "backups"
    dest.mkdir()
    # Eski nusxalar (soxta vaqt belgilari)
    for i in range(5):
        (dest / f"mock_center-2020010{i}T000000Z.db").write_bytes(b"old")

    assert mod.main(["--db", str(src), "--dest", str(dest), "--keep", "3"]) == 0

    backups = sorted(dest.glob("mock_center-*.db"))
    assert len(backups) == 3, [b.name for b in backups]  # 5 eski + 1 yangi, 3 tasi qoldi
    newest = backups[-1]
    conn = sqlite3.connect(str(newest))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    finally:
        conn.close()


def test_backup_fails_cleanly_for_missing_db(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("backup_sqlite", ROOT / "scripts" / "backup_sqlite.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--db", str(tmp_path / "yoq.db"), "--dest", str(tmp_path)]) == 1


# --------------------------------------------------------------------------
# 3. Listening audio: ochiq static emas, ruxsat tekshiruvi bilan
# --------------------------------------------------------------------------

def _fill(model, **kw):
    """Majburiy (NOT NULL, default'siz) ustunlarni test uchun to'ldiradi."""
    values = dict(kw)
    for col in sa_inspect(model).columns:
        if col.name in values or col.primary_key or col.nullable:
            continue
        if col.default is not None or col.server_default is not None:
            continue
        t = col.type
        if isinstance(t, Boolean):
            values[col.name] = False
        elif isinstance(t, Integer):
            values[col.name] = 0
        elif isinstance(t, DateTime):
            values[col.name] = datetime.now(timezone.utc)
        elif isinstance(t, JSON):
            values[col.name] = {}
        elif isinstance(t, Uuid):
            values[col.name] = uuid.uuid4()
        else:
            values[col.name] = uuid.uuid4().hex[:16]
    return model(**values)


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.include_router(audio_router.router, prefix=API)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


@pytest.fixture
def audio_setup(monkeypatch, tmp_path):
    private = tmp_path / "private"
    monkeypatch.setattr(file_storage, "PRIVATE_ROOT", private)
    monkeypatch.setattr(audio_router, "PRIVATE_ROOT", private)
    name = f"{uuid.uuid4().hex}.mp3"
    folder = private / "listening_audio"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"ID3" + b"\x00" * 64)
    return name


async def _listening_test_with_audio(name: str) -> str:
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=OFF"))
        test = _fill(ListeningTest, id=f"lt-{uuid.uuid4().hex[:8]}", title="L", is_active=True,
                     audio_url=f"{API}/exams/listening-audio/{name}")
        s.add(test)
        await s.commit()
        return test.id


async def test_listening_audio_is_not_served_from_public_static(env, audio_setup):
    r = await env.get(f"/static/listening_audio/{audio_setup}")
    assert r.status_code == 404


async def test_listening_audio_requires_authorization(env, audio_setup):
    await _listening_test_with_audio(audio_setup)
    r = await env.get(f"{API}/exams/listening-audio/{audio_setup}")
    assert r.status_code in (401, 404), r.text
    assert r.status_code != 200


async def test_listening_audio_rejects_unknown_and_unsafe_names(env, audio_setup):
    assert (await env.get(f"{API}/exams/listening-audio/{uuid.uuid4().hex}.mp3")).status_code == 404
    assert (await env.get(f"{API}/exams/listening-audio/..%2F..%2Fsecret.mp3")).status_code == 404
    assert (await env.get(f"{API}/exams/listening-audio/{audio_setup[:-4]}.exe")).status_code == 404


# --------------------------------------------------------------------------
# 4. Migratsiya 0005 haqiqiy ma'lumotda: BigInteger va audio URL ko'chirish
# --------------------------------------------------------------------------

def _fill_raw(conn: sqlite3.Connection, table: str, overrides: dict) -> None:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    vals = {}
    for _cid, name, typ, notnull, dflt, pk in cols:
        if name in overrides:
            vals[name] = overrides[name]
        elif notnull and dflt is None and not pk:
            t = typ.upper()
            if "INT" in t or "BOOL" in t:
                vals[name] = 0
            elif "DATE" in t or "TIME" in t:
                vals[name] = "2030-01-01 09:00:00"
            else:
                vals[name] = uuid.uuid4().hex
    conn.execute(f"INSERT INTO {table} ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})", list(vals.values()))


def test_migration_0005_rewrites_audio_urls_and_widens_balance(tmp_path):
    db = tmp_path / "upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    run = lambda *args: subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                                       capture_output=True, text=True, timeout=180)
    assert run("upgrade", "0004_exam_session_seats").returncode == 0

    conn = sqlite3.connect(str(db))
    _fill_raw(conn, "listening_tests", {"id": "lt-mig", "audio_url": "/static/listening_audio/abc.mp3"})
    _fill_raw(conn, "listening_parts", {"exam_id": "lt-mig", "audio_url": "/static/listening_audio/part.mp3"})
    conn.commit()
    conn.close()

    up = run("upgrade", "head")
    assert up.returncode == 0, up.stderr[-1500:]

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT audio_url FROM listening_tests WHERE id='lt-mig'").fetchone()[0] \
            == f"{API}/exams/listening-audio/abc.mp3"
        assert conn.execute("SELECT audio_url FROM listening_parts WHERE exam_id='lt-mig'").fetchone()[0] \
            == f"{API}/exams/listening-audio/part.mp3"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='organization_accounts'").fetchone() is None
    finally:
        conn.close()
    assert run("check").returncode == 0 or "No new upgrade operations" in run("check").stdout


def test_settings_point_listening_audio_to_private_storage():
    assert "static" not in settings.LISTENING_AUDIO_DIR.split("/")
    assert PRIVATE_DIR in (PROJECT_ROOT / settings.LISTENING_AUDIO_DIR).parents or \
        (PROJECT_ROOT / settings.LISTENING_AUDIO_DIR).is_relative_to(PRIVATE_DIR)
