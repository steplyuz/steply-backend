from app.core.config import settings


class TelegramService:
    """Telegram registration deep-link builder.

    The bot itself is a separate aiogram process. The backend never stores or
    exposes a Telegram application session through this helper.
    """

    @classmethod
    def build_start_link(cls, start_token: str) -> str:
        username = settings.TELEGRAM_BOT_USERNAME.lstrip("@")
        return f"https://t.me/{username}?start={start_token}"
