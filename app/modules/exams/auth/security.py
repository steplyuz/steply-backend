"""
exams_auth/security.py
========================
Exams uchun MUSTAQIL JWT siyosati.

`app.core.security` dagi asosiy Steply access-token bilan bir xil
SECRET_KEY/ALGORITHM ishlatiladi (bitta serverda ikkita alohida imzo
kaliti saqlashning amaliy foydasi yo'q — imzo kaliti bir xil bo'lsa ham,
quyidagilar tokenlarni bir-birining o'rniga ishlatib bo'lmaydigan
qiladi):

  1. Butunlay boshqa ISSUER ("examsauth.<domain>" vs "auth.<domain>")
  2. Butunlay boshqa AUDIENCE ("examsauth.<domain>" — asosiy AUDIENCE
     ro'yxatida ("api.<domain>", "app.<domain>", "cefr.<domain>",
     "<domain>") bunday audience yo'q, shuning uchun asosiy
     `decode_token` bu tokenni "InvalidAudienceError" bilan rad etadi)
  3. Boshqa "type" claim qiymati ("exams_access")

Natijada: asosiy Steply token bilan Exams endpointiga kirib bo'lmaydi va
Exams token bilan asosiy Steply endpointiga kirib bo'lmaydi — ikkalasi
ham decode bosqichida rad etiladi (band 6: "Exams tokenini boshqa Steply
auth tokenlari bilan almashtirib ishlatish mumkin bo'lmasin").
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, status

from app.core.config import settings

DOMAIN = settings.AUTH_DOMAIN
ISSUER = f"examsauth.{DOMAIN}"
AUDIENCE = [f"examsauth.{DOMAIN}"]

# Asosiy Steply access tokeni 60 daqiqa (ACCESS_TOKEN_MINUTES). Mock
# imtihonlar bir necha soat davom etishi mumkin (MockExam.duration_minutes
# odatda 180), shuning uchun Exams access-tokeni ancha uzunroq — lekin
# imtihon markazidagi umumiy/sinov kompyuterlarida qolib ketish xavfini
# kamaytirish uchun refresh muddati asosiy Steply'dan (30 kun) ANCHA
# qisqa qilib olindi.
EXAMS_ACCESS_TOKEN_MINUTES = 240
EXAMS_REFRESH_TOKEN_DAYS = 7


def create_exams_access_token(user_id: "uuid.UUID | str", extra_data: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "iss": ISSUER,
        "sub": str(user_id),
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "jti": str(uuid.uuid4()),
        "type": "exams_access",
        "exp": now + timedelta(minutes=EXAMS_ACCESS_TOKEN_MINUTES),
    }
    if extra_data:
        protected = {"iss", "sub", "aud", "iat", "nbf", "jti", "type", "exp"}
        if protected.intersection(extra_data):
            raise ValueError("Protected JWT claims cannot be overridden")
        payload.update(extra_data)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_exams_token(token: str, token_type: str = "exams_access") -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
                "require": ["exp", "iat", "iss", "sub", "aud", "jti", "type"],
            },
        )
        if payload.get("type") != token_type:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token turi noto'g'ri")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sessiya muddati tugagan, qaytadan kiring")
    except jwt.InvalidAudienceError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Bu token Exams tizimi uchun emas")
    except jwt.InvalidIssuerError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Noma'lum manbadan kelgan token")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token yaroqsiz")
