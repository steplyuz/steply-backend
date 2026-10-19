from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENV: str = "local"
    PROJECT_NAME: str = "Mock Center API"
    DATABASE_URL: str = "sqlite+aiosqlite:///./mock_center.db"

    SECRET_KEY: str = "CHANGE_ME_IN_ENV"
    ALGORITHM: str = "HS256"
    AUTO_CREATE_SCHEMA: bool = True
    # Default faqat lokal host. Production'da aniq ro'yxat talab qilinadi (validator).
    TRUSTED_HOSTS: Any = ["localhost", "127.0.0.1", "testserver"]
    ADMIN_SECRET_KEY: str = "CHANGE_ME_ADMIN_SECRET"
    ADMIN_SESSION_COOKIE_SECURE: bool = False

    # JWT domen sozlamalari — issuer/audience shundan hosil qilinadi (core/security.py).
    AUTH_DOMAIN: str = "steply.uz"

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = "steply_bot"
    TELEGRAM_BILLING_ADMIN_CHAT_ID: str = ""
    TELEGRAM_BILLING_ADMIN_USER_ID: str = ""
    TELEGRAM_BILLING_ADMIN_TELEGRAM_USER_ID: str = ""

    # Telegram Login OIDC (Authorization Code + PKCE)
    TELEGRAM_CLIENT_ID: str = ""
    TELEGRAM_CLIENT_SECRET: str = ""
    TELEGRAM_REDIRECT_URI: str = ""
    TELEGRAM_OIDC_DISCOVERY_URL: str = "https://oauth.telegram.org/.well-known/openid-configuration"
    TELEGRAM_OIDC_STATE_TTL_SECONDS: int = 600
    TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS: float = 10.0
    TELEGRAM_OIDC_CLOCK_SKEW_SECONDS: int = 60
    TELEGRAM_OIDC_SCOPE: str = "openid profile phone"

    # Google Sign-In (Google Identity Services, id_token JS orqali keladi; redirect yo'q)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_JWKS_URL: str = "https://www.googleapis.com/oauth2/v3/certs"
    GOOGLE_HTTP_TIMEOUT_SECONDS: float = 10.0
    ESKIZ_EMAIL: str = ""
    ESKIZ_PASSWORD: str = ""
    INTERNAL_API_TOKEN: str = ""
    OPENAI_API_KEY: str = ""
    # OpenAI-compatible providers (for example Amazon Bedrock Mantle). Empty = OpenAI default.
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # MUHIM: pydantic-settings v2 da List[str] bo'lsa, .env'dan o'qiyotganda
    # avvalo JSON sifatida parse qilmoqchi bo'ladi va vergul-ajratilgan
    # qiymat uchun JSONDecodeError beradi. Yechim: Any tipida e'lon qilib,
    # field_validator orqali o'zimiz parse qilamiz.
    ALLOWED_ORIGINS: Any = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://exams.steply.uz",
        "https://speaking.steply.uz",
    ]

    ACCESS_TOKEN_MINUTES: int = 60
    REFRESH_TOKEN_DAYS: int = 30
    COOKIE_DOMAIN: Optional[str] = None
    # Diqqat: True bo'lsa cookie faqat HTTPS ustida ishlaydi (SameSite=None talabi).
    # Lokal http://localhost muhitida buzilmasligi uchun default False — production'da
    # .env orqali COOKIE_SECURE=true qilib qo'yish SHART.
    COOKIE_SECURE: bool = False
    COOKIE_HTTPONLY: bool = True
    # api.steply.uz, exams.steply.uz and the web apps are same-site. Lax is
    # the safer default; set "none" only for a genuinely cross-site frontend.
    COOKIE_SAMESITE: str = "lax"
    DEBUG: bool = False

    SPEAKING_AUDIO_DIR: str = "storage/private/speaking"
    # Ochiq static emas: audio ruxsat tekshiruvi orqali beriladi (/exams/listening-audio/{file}).
    LISTENING_AUDIO_DIR: str = "storage/private/listening_audio"
    PRIVATE_STORAGE_DIR: str = "storage/private"
    PUBLIC_BASE_URL: str = ""
    # Public, immutable media assets are served by Nginx at this dedicated origin.
    MEDIA_DOMAIN: str = "https://static.steply.uz"
    FRONTEND_URL: str = "http://localhost:3000"
    SPEAKING_ALLOWED_AUDIO_EXTENSIONS: Any = [".mp3", ".m4a", ".wav", ".ogg", ".webm"]
    SPEAKING_MAX_AUDIO_MB: int = 25

    RESULT_SMS_ENABLED: bool = False
    RESULT_SMS_SENDER: str = "4546"

    # --- Exams (nomzod) kirishi ---------------------------------------------
    # MK ID (MK-DDMMYY-NNN) tug'ilgan sanadan va 3 ta tasodifiy raqamdan iborat,
    # ya'ni har sana uchun faqat 1000 variant. Shuning uchun uni yagona kalit
    # sifatida ishlatish zaif. Frontend tasodifiy `exam_id` (exam_access_id) ga
    # o'tgach, MK ID bilan kirishni butunlay o'chirib qo'ying.
    EXAMS_MK_ID_LOGIN_ENABLED: bool = False
    # MK ID bilan kirishda ikkinchi omil: nomzod telefon raqamining oxirgi 4 raqami.
    # Yoqilsa so'rovda `phone_last4` majburiy bo'ladi va noto'g'ri urinishlar
    # registratsiya bo'yicha cheklanadi.
    # Standart: MK ID bilan kirishda telefonning oxirgi 4 raqami talab qilinadi.
    # Ro'yxatdan o'tishda telefon majburiy bo'ladi (aks holda ikkinchi omil bo'lmaydi).
    EXAMS_MK_ID_REQUIRE_PHONE_LAST4: bool = True
    # Ochiq /apply uchun CAPTCHA (Cloudflare Turnstile). Productionda true bo'lishi shart.
    APPLY_CAPTCHA_ENABLED: bool = False
    TURNSTILE_SECRET_KEY: str = ""
    # Kirish vaqt oynasi (None = tekshirilmaydi): seans boshlanishidan necha
    # daqiqa oldin ochiladi / boshlanganidan necha daqiqa o'tgach yopiladi.
    EXAMS_LOGIN_OPENS_MINUTES_BEFORE: int | None = None
    EXAMS_LOGIN_CLOSES_MINUTES_AFTER: int | None = None

    # X-Forwarded-For headeri faqat shu bayroq yoqilganda ishonchli hisoblanadi
    # (server ishonchli reverse-proxy — nginx/traefik — ortida bo'lib, u mijoz
    # yuborgan XFF'ni har doim o'chirib/qayta yozgandagina True qiling).
    # Aks holda, bu header mijoz tomonidan to'liq soxtalashtiriladi va IP
    # asosidagi rate-limit'ni chetlab o'tish uchun ishlatilishi mumkin.
    TRUST_PROXY_HEADERS: bool = False
    # Rate-limit va throttle hisoblagichlari uchun storage. Bir nechta worker yoki
    # server bo'lsa "redis://host:6379/0" qiling; aks holda har worker o'z limitiga ega.
    RATE_LIMIT_STORAGE_URI: str = "memory://"
    # Telegram botni API jarayoni ichida ishga tushirish (faqat lokal dev uchun).
    # Productionda bot alohida jarayon: `python bot.py` (docs/DEPLOY.md).
    RUN_TELEGRAM_BOT_IN_API: bool = False
    # /admin paneliga ruxsat etilgan IP'lar (vergul bilan). Bo'sh bo'lsa cheklov yo'q.
    ADMIN_ALLOWED_IPS: Any = []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() in {"prod", "production"}

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.is_production:
            if self.SECRET_KEY == "CHANGE_ME_IN_ENV" or len(self.SECRET_KEY) < 32:
                raise ValueError("Production uchun SECRET_KEY kamida 32 belgili kuchli qiymat bo'lishi kerak")
            if self.ADMIN_SECRET_KEY == "CHANGE_ME_ADMIN_SECRET" or len(self.ADMIN_SECRET_KEY) < 32:
                raise ValueError("Production uchun ADMIN_SECRET_KEY kamida 32 belgili kuchli qiymat bo'lishi kerak")
            if self.DEBUG:
                raise ValueError("Production uchun DEBUG=false bo'lishi kerak")
            if self.AUTO_CREATE_SCHEMA:
                raise ValueError("Productionda AUTO_CREATE_SCHEMA=false bo'lishi kerak; schema faqat Alembic orqali boshqariladi")
            if not self.INTERNAL_API_TOKEN:
                raise ValueError("Production uchun INTERNAL_API_TOKEN o'rnatilishi shart")
            if not self.COOKIE_SECURE or not self.ADMIN_SESSION_COOKIE_SECURE:
                raise ValueError("Production uchun barcha autentifikatsiya session cookie'lari Secure=true bo'lishi kerak")
            if self.COOKIE_SAMESITE not in {"lax", "strict", "none"}:
                raise ValueError("COOKIE_SAMESITE lax, strict yoki none bo'lishi kerak")
            if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE:
                raise ValueError("SameSite=None uchun COOKIE_SECURE=true bo'lishi kerak")
            if not self.ALLOWED_ORIGINS:
                raise ValueError("Production uchun ALLOWED_ORIGINS bo'sh bo'lmasligi kerak")
            if any(origin == "*" or not origin.lower().startswith("https://") for origin in self.ALLOWED_ORIGINS):
                raise ValueError("Production ALLOWED_ORIGINS faqat aniq HTTPS originlardan iborat bo'lishi kerak")
            if any("localhost" in origin.lower() or "127.0.0.1" in origin for origin in self.ALLOWED_ORIGINS):
                raise ValueError("Production ALLOWED_ORIGINS localhost/127.0.0.1 ni o'z ichiga olmasligi kerak")
            if not self.TRUSTED_HOSTS:
                raise ValueError("Production uchun TRUSTED_HOSTS sozlanishi shart")
            if "*" in self.TRUSTED_HOSTS:
                raise ValueError("Production TRUSTED_HOSTS wildcard bo'lishi mumkin emas")
            if not self.PUBLIC_BASE_URL or not self.PUBLIC_BASE_URL.lower().startswith("https://"):
                raise ValueError("Production PUBLIC_BASE_URL HTTPS bo'lishi shart")
            if not self.FRONTEND_URL or not self.FRONTEND_URL.lower().startswith("https://"):
                raise ValueError("Production FRONTEND_URL HTTPS bo'lishi shart")
            if not self.TELEGRAM_CLIENT_ID or not self.TELEGRAM_CLIENT_SECRET:
                raise ValueError("Production Telegram OIDC client credentials o'rnatilishi shart")
            if not self.TELEGRAM_REDIRECT_URI or not self.TELEGRAM_REDIRECT_URI.lower().startswith("https://"):
                raise ValueError("Production TELEGRAM_REDIRECT_URI HTTPS bo'lishi shart")
            if not self.DATABASE_URL.startswith("sqlite+aiosqlite:///"):
                raise ValueError("Bu deployment modeli uchun production DATABASE_URL sqlite+aiosqlite bo'lishi shart")
            db_path = Path(urlparse(self.DATABASE_URL).path)
            if not db_path.is_absolute():
                raise ValueError("Production SQLite DATABASE_URL absolute/private path bo'lishi shart")
            if any(part in {"tmp", "static", "public"} for part in db_path.parts):
                raise ValueError("Production SQLite DB public yoki temporary katalogda bo'lishi mumkin emas")
            if self.COOKIE_DOMAIN and self.COOKIE_DOMAIN.startswith("http"):
                raise ValueError("COOKIE_DOMAIN URL emas, faqat cookie domain bo'lishi kerak")
        return self

    @field_validator("ALLOWED_ORIGINS", "SPEAKING_ALLOWED_AUDIO_EXTENSIONS", "TRUSTED_HOSTS", "ADMIN_ALLOWED_IPS", mode="before")
    @classmethod
    def parse_lists(cls, value: Any) -> List[str]:
        """
        .env'dan vergul bilan ajratilgan qiymatlarni ro'yxatga aylantiradi.
        JSON array formatini ham qabul qiladi: ["url1", "url2"]
        pydantic-settings v2 da List[str] oldindan JSON parse qilinadi,
        shu uchun Any tipida e'lon qilingan — bu validator o'zi hal qiladi.
        """
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                import json
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    raise ValueError("JSON ro'yxat formati noto'g'ri (masalan: [\"https://steply.uz\"])")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []


settings = Settings()
