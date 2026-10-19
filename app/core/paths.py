"""Loyiha yo'llari — joriy papkaga (cwd) bog'liq emas.

Ilgari `static/...` va `storage/private/...` nisbiy yo'llar edi: server boshqa papkadan
ishga tushsa, fayllar noto'g'ri joyga yozilar yoki o'qilmasdi (shu sababli testlarda
`chdir` workaround'i kerak edi). Endi barcha yo'llar PROJECT_ROOT dan hisoblanadi.
"""
from pathlib import Path

from app.core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


STATIC_DIR = PROJECT_ROOT / "static"
PRIVATE_DIR = project_path(settings.PRIVATE_STORAGE_DIR)
LISTENING_AUDIO_DIR = project_path(settings.LISTENING_AUDIO_DIR)
