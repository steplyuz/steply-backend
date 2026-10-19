"""SQLite zaxira nusxa: onlayn, izchil (sqlite3 backup API), tekshirilgan, eskilari tozalanadi.

Ishlatish:
    python scripts/backup_sqlite.py --db storage/mock_center.db --dest /backup/steply --keep 14

Cron (har kuni 03:00):
    0 3 * * * cd /opt/steply && ./venv/bin/python scripts/backup_sqlite.py --db storage/mock_center.db --dest /backup/steply

Nusxa API server ishlab turganda ham olinadi (WAL/rollback jurnali bilan to'g'ri ishlaydi).
Har nusxa `PRAGMA integrity_check` dan o'tmasa, u o'chiriladi va skript xato bilan tugaydi.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def make_backup(src: Path, dest_dir: Path) -> Path:
    if not src.is_file():
        raise FileNotFoundError(f"Baza topilmadi: {src}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = dest_dir / f"{src.stem}-{stamp}.db"
    tmp = target.with_suffix(".db.tmp")

    source = sqlite3.connect(str(src))
    try:
        dest = sqlite3.connect(str(tmp))
        try:
            source.backup(dest)  # izchil, onlayn nusxa
        finally:
            dest.close()
    finally:
        source.close()

    check = sqlite3.connect(str(tmp))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Zaxira nusxa buzilgan: {result}")

    tmp.replace(target)
    return target


def prune(dest_dir: Path, stem: str, keep: int) -> list[Path]:
    backups = sorted(dest_dir.glob(f"{stem}-*.db"), key=lambda p: p.name)
    stale = backups[:-keep] if keep > 0 else []
    for path in stale:
        path.unlink(missing_ok=True)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite zaxira nusxa")
    parser.add_argument("--db", required=True, type=Path, help="SQLite baza fayli")
    parser.add_argument("--dest", required=True, type=Path, help="Zaxira papkasi")
    parser.add_argument("--keep", type=int, default=14, help="Nechta nusxa saqlansin (0 = hammasi)")
    args = parser.parse_args(argv)
    try:
        target = make_backup(args.db, args.dest)
        removed = prune(args.dest, args.db.stem, args.keep)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"XATO: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {target}")
    if removed:
        print(f"Eski nusxalar o'chirildi: {len(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
