import logging
from logging.config import dictConfig

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}

def setup_logging():
    dictConfig(LOGGING_CONFIG)
    # XAVFSIZLIK: httpx INFO darajasida to'liq URL'ni log qiladi. Telegram Bot API
    # URL'i `.../bot<TELEGRAM_BOT_TOKEN>/sendMessage` — ya'ni bot tokeni logga
    # tushib ketardi. Shu loggerlarni WARNING'ga tushiramiz.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
