import jwt
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from app.core.config import settings
from typing import Optional, Dict, Any

# Argon2id. Parametrlar passlib davridagi bilan bir xil (time=2, memory=100 MiB, parallelism=4),
# shuning uchun mavjud hashlar o'zgarishsiz tekshiriladi (parametrlar hash ichida saqlanadi).
_hasher = PasswordHasher(time_cost=2, memory_cost=102400, parallelism=4)

# KONSTANTALAR - Domen cheklovlari uchun
# Ilgari bu yerda domen hardcode qilingan edi (.env/settings'dan mustaqil holda),
# endi settings.AUTH_DOMAIN'dan olinadi — CORS/COOKIE_DOMAIN bilan bitta manbadan
# boshqariladi, deploy paytida domen almashtirilsa JWT tekshiruvi ham mos keladi.
DOMAIN = settings.AUTH_DOMAIN
ISSUER = f"auth.{DOMAIN}"        # Tokenni kim berdi
AUDIENCE = [f"api.{DOMAIN}", f"app.{DOMAIN}", f"cefr.{DOMAIN}", DOMAIN]  # Token kimlar uchun


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerificationError, InvalidHashError):
        return False
# =====================
# JWT TOKENS (DOMAIN-LOCKED)
# =====================

def create_access_token(user_id: "uuid.UUID | str", extra_data: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,                 # Faqat auth.steply.uz tomonidan
        "sub": str(user_id),           # User ID
        "aud": AUDIENCE,               # Faqat steply.uz ekotizimi uchun
        "iat": now,
        "nbf": now,
        "jti": str(uuid.uuid4()),      # Replay attackga qarshi unikal ID
        "type": "access",
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
    }
    
    if extra_data:
        # Security-critical JWT claims are issuer-controlled and may not be
        # overridden by callers. Business claims (for example role/sid) are
        # merged only after the fixed claims are protected.
        protected = {"iss", "sub", "aud", "iat", "nbf", "jti", "type", "exp"}
        if protected.intersection(extra_data):
            raise ValueError("Protected JWT claims cannot be overridden")
        payload.update(extra_data)

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str, token_type: str) -> dict:
    """
    Tokenni dekod qilishda issuer va audience'ni qat'iy tekshiradi.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=AUDIENCE,         # Audience tekshiruvi
            issuer=ISSUER,             # Issuer tekshiruvi
            options={
                "verify_exp": True, 
                "verify_iat": True, 
                "verify_nbf": True,
                "require": ["exp", "iat", "iss", "sub", "aud", "jti", "type"] # Bu maydonlar bo'lishi shart
            }
        )

        if payload.get("type") != token_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Token turi noto‘g‘ri"
            )

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token muddati tugagan")
    except jwt.InvalidAudienceError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Ushbu token {DOMAIN} tizimi uchun emas!")
    except jwt.InvalidIssuerError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Noma'lum manbadan kelgan token!")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token yaroqsiz")