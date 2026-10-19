"""Small, local-only private file storage helpers.

Physical names are server-generated and all private reads are containment-checked.
This intentionally does not introduce a second storage backend.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException

from app.core.paths import PRIVATE_DIR

PRIVATE_ROOT = PRIVATE_DIR  # absolyut (cwd'ga bog'liq emas)
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$", re.I)


def private_path(category: str, *parts: str) -> Path:
    if not category or any(not p or "\\" in p or Path(p).name != p or p in {".", ".."} for p in (category, *parts)):
        raise ValueError("unsafe storage path component")
    root = (PRIVATE_ROOT / category).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root.joinpath(*parts).resolve()
    # Ichki papkalar (masalan speaking/submissions) ham yaratiladi: ilgari faqat `root`
    # yaratilardi va toza serverda birinchi audio yozish FileNotFoundError berardi.
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def ensure_private_file(path: Path, category: str) -> Path:
    root = (PRIVATE_ROOT / category).resolve()
    candidate = path.resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(404, "Fayl topilmadi")
    if not candidate.is_file():
        raise HTTPException(404, "Fayl topilmadi")
    return candidate


def safe_suffix(suffix: str, allowed: Iterable[str]) -> str:
    suffix = (suffix or "").lower()
    allowed_set = {str(x).lower() for x in allowed}
    if suffix not in allowed_set or not _SAFE_SUFFIX.fullmatch(suffix):
        raise HTTPException(415, "Ruxsat etilmagan fayl turi")
    return suffix


def generated_name(suffix: str) -> str:
    return f"{secrets.token_hex(16)}{suffix}"


def safe_filename_for_header(name: str, fallback: str = "download") -> str:
    base = Path(name or fallback).name.replace("\r", "").replace("\n", "")
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or fallback
    return base[:180]


