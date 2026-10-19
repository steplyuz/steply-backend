import hmac

from fastapi import Header, HTTPException, status
from app.core.config import settings

async def verify_internal_token(
    x_internal_token: str = Header(..., alias="X-Internal-Token")
):
    # INTERNAL_API_TOKEN sozlanmagan (bo'sh) bo'lsa — har doim rad etamiz.
    # Aks holda, bo'sh X-Internal-Token header yuborilganda ichki endpointlar
    # tasodifan himoyasiz qolishi mumkin.
    # bytes bilan solishtiramiz: str'da ASCII bo'lmagan belgi bo'lsa
    # hmac.compare_digest TypeError beradi (-> 500). Endi 403 qaytadi.
    if not settings.INTERNAL_API_TOKEN or not hmac.compare_digest(
        x_internal_token.encode("utf-8"), settings.INTERNAL_API_TOKEN.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal token"
        )
