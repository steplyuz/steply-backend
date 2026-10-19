from app.core.config import settings
from app.core.media import media_url


def test_public_static_path_uses_media_domain():
    assert media_url('/static/listening_maps/map.png') == f"{settings.MEDIA_DOMAIN.rstrip('/')}/static/listening_maps/map.png"


def test_existing_full_media_url_is_preserved():
    value = 'https://cdn.example.test/static/old.png'
    assert media_url(value) == value


def test_private_api_path_is_not_moved_to_public_media_domain():
    value = '/api/v1/exams/listening-audio/private.mp3'
    assert media_url(value) == value
