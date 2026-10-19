"""Helpers for public media URLs; private media must continue through authorized API routes."""
from urllib.parse import urlsplit

from app.core.config import settings


def media_url(path: str) -> str:
    """Return a canonical URL for a public static media path, preserving legacy full URLs."""
    value = (path or "").strip()
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    if not value.startswith("/static/"):
        # Do not accidentally publish private API paths on the static domain.
        return value
    return f"{settings.MEDIA_DOMAIN.rstrip('/')}{value}"
