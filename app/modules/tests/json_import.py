"""Admin JSON import uchun umumiy yordamchi (Reading/Listening).

Ilgari `File(None)` + `Body(None)` bitta endpointda birga e'lon qilingan edi: FastAPI butun
so'rovni form deb o'qir va oddiy `application/json` body hech qachon `body` ga tushmasdi
(har doim 400 "JSON fayl yoki JSON body yuborish kerak"). Endi content-type'ga qarab
qo'lda o'qiladi: `multipart/form-data` (`file`) yoki `application/json`.
"""
import json
from typing import Any

from fastapi import HTTPException, Request

MAX_JSON_BYTES = 10 * 1024 * 1024

IMPORT_OPENAPI_EXTRA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": {"type": "object"}},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {"file": {"type": "string", "format": "binary"}},
                    "required": ["file"],
                }
            },
        },
    }
}


async def read_import_json(request: Request) -> Any:
    """So'rovdan JSON obyektni o'qiydi (multipart `file` yoki JSON body)."""
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(400, "multipart so'rovda 'file' maydoni bo'lishi kerak")
        filename = (getattr(file, "filename", "") or "").lower()
        if not filename.endswith(".json") and getattr(file, "content_type", None) not in {"application/json", "text/json"}:
            raise HTTPException(400, "Faqat JSON fayl yuklang")
        raw_bytes = await file.read()
    else:
        raw_bytes = await request.body()
    if not raw_bytes:
        raise HTTPException(400, "JSON fayl (file) yoki JSON body yuborish kerak")
    if len(raw_bytes) > MAX_JSON_BYTES:
        raise HTTPException(413, "JSON hajmi 10 MB dan oshmasligi kerak")
    try:
        return json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "JSON noto'g'ri formatda") from exc
