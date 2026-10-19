"""Candidate rasmi: faqat o'zimiz yuklagan va qayta kodlagan fayl qabul qilinadi.

Ilgari photo_url erkin matn edi (data: URL bazani shishirardi, istalgan tashqi havola
yozilardi). Endi: rasm yuklanadi, tekshiriladi, JPEG'ga qayta kodlanadi (EXIF va
boshqa metama'lumotlar olib tashlanadi), tasodifiy nomda saqlanadi. Ro'yxatdan
o'tishda faqat shu yuklash bergan URL qabul qilinadi.

Ochiqlik haqida: fayllar /static/applicant_photos/ ostida (tasodifiy 32 belgili nom).
Bu admin/tashkilot uchun ko'rinish uchun qulay, lekin to'liq yopiq emas — shaxsiy
ma'lumot sifatida yopiq saqlash (ruxsat tekshiradigan endpoint) 6-bosqichda hal qilinadi.
"""
import io
import re
import uuid

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.paths import STATIC_DIR

PHOTO_DIR = STATIC_DIR / "applicant_photos"
PHOTO_URL_PREFIX = "/static/applicant_photos/"
_PHOTO_URL_RE = re.compile(r"^/static/applicant_photos/([0-9a-f]{32})\.jpg$")
MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 1024
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def is_own_photo_url(url: str | None) -> bool:
    match = _PHOTO_URL_RE.fullmatch(url or "")
    return bool(match) and (PHOTO_DIR / f"{match.group(1)}.jpg").is_file()


async def save_applicant_photo(upload: UploadFile) -> str:
    data = await upload.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Rasm hajmi 5 MB dan oshmasligi kerak")
    if not data:
        raise HTTPException(400, "Rasm bo'sh")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in _ALLOWED_FORMATS:
                raise HTTPException(400, "Faqat JPEG, PNG yoki WEBP formatdagi rasm qabul qilinadi")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            rgb = img.convert("RGB")  # alpha va EXIF yo'qoladi
        rgb.thumbnail((MAX_SIDE, MAX_SIDE))
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=85)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "Rasm noto'g'ri yoki buzilgan") from exc

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    (PHOTO_DIR / name).write_bytes(buffer.getvalue())
    return PHOTO_URL_PREFIX + name
