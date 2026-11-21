import pytest

from app.modules.notifications.service import backoff_seconds, placeholders, render


def test_render_replaces_placeholders():
    assert render("Salom, {{name}}! Kod: {{ code }}", {"name": "Ali", "code": "123"}) == "Salom, Ali! Kod: 123"


def test_render_missing_variable_raises():
    with pytest.raises(ValueError):
        render("Salom {{name}}", {})


def test_placeholders_are_detected():
    assert placeholders("{{a}} x {{ b }} y") == {"a", "b"}


def test_backoff_grows_and_is_capped_at_one_hour():
    assert backoff_seconds(1) == 60
    assert backoff_seconds(2) == 120
    assert backoff_seconds(3) == 240
    assert backoff_seconds(20) == 3600


def test_unsubscribe_token_roundtrip():
    from app.modules.notifications.service import make_unsubscribe_token, parse_unsubscribe_token

    token = make_unsubscribe_token("email", "user@example.uz")
    assert parse_unsubscribe_token(token) == ("email", "user@example.uz")


def test_unsubscribe_token_tampered_is_rejected():
    from app.modules.notifications.service import make_unsubscribe_token, parse_unsubscribe_token

    token = make_unsubscribe_token("email", "user@example.uz")
    with pytest.raises(ValueError):
        parse_unsubscribe_token(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))


def test_gmail_raw_message_carries_unsubscribe_headers():
    import base64

    from app.modules.notifications.gmail import build_raw_message

    raw = build_raw_message("no-reply@steply.uz", "Steply", "u@example.uz", "Salom", "Matn", "https://api.x/u?t=1")
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8", errors="replace")
    assert "List-Unsubscribe: <https://api.x/u?t=1>" in decoded
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in decoded
