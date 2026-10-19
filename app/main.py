
from contextlib import asynccontextmanager
import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.errors import AppError, make_code
from app.core.database import engine, init_db
from app.core.rate_limit import (
    limiter,
    rate_limit_error_handler,
    _get_ip,
)
from app.core.throttle import FailureThrottle
from app.core.csrf import csrf_middleware
from app.core.paths import STATIC_DIR
from app.core.logging import setup_logging

from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as user_router

from app.modules.exams.mock.router import router as mock_router
from app.modules.exams.mock.public_router import (
    router as mock_public_router,
)
from app.modules.exams.mock.apply_router import router as apply_router
from app.modules.exams.mock.answer_sheets_router import (
    router as answer_sheets_router,
)

from app.modules.tests.reading.router import (
    router as reading_router,
    admin_router as admin_reading_router,
)
from app.modules.tests.listening.router import (
    router as listening_router,
    admin_router as admin_listening_router,
)
from app.modules.tests.listening.audio_router import (
    router as listening_audio_router,
)
from app.modules.tests.writing.router import (
    router as writing_router,
    admin_router as admin_writing_router,
    evaluator_router as evaluator_writing_router,
)
from app.modules.tests.speaking.router import (
    router as speaking_router,
    admin_router as admin_speaking_router,
    evaluator_router as evaluator_speaking_router,
)

from app.modules.practice.router import router as practice_router
from app.modules.admin.router import router as admin_router

from app.modules.billing.router import (
    router as billing_router,
    admin_router as admin_billing_router,
)
from app.modules.analysis.router import (
    router as analysis_router,
    admin_router as admin_analysis_router,
)
from app.modules.exams.auth.router import (
    router as exams_auth_router,
    exams_router,
)

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)


def configure_logging() -> None:
    """
    Loglarni PowerShell terminaliga darhol chiqarish.
    API kalitlari, tokenlar va request body loglanmaydi.
    """
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    logging.getLogger("steply").setLevel(logging.DEBUG)
    logging.getLogger("steply.debug").setLevel(logging.DEBUG)

    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    # HTTP kutubxonalarining maxfiy ma'lumotlarni chiqarishini
    # oldini olish uchun ularning batafsil loglari yoqilmaydi.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


configure_logging()

logger = logging.getLogger("steply.debug")


# ---------------------------------------------------------------------
# FastAdmin environment
# FastAdmin import qilinishidan OLDIN environment sozlanadi.
# ---------------------------------------------------------------------

os.environ.setdefault("ADMIN_USER_MODEL", "User")
os.environ.setdefault("ADMIN_USER_MODEL_USERNAME_FIELD", "id")
os.environ.setdefault("ADMIN_SECRET_KEY", settings.ADMIN_SECRET_KEY)
os.environ.setdefault(
    "ADMIN_SESSION_COOKIE_SECURE",
    "true" if settings.ADMIN_SESSION_COOKIE_SECURE else "false",
)

from fastadmin import fastapi_app as admin_app  # noqa: E402

import app.modules.fastadmin_panel.admin  # noqa: E402,F401


# ---------------------------------------------------------------------
# Production database schema verification
# ---------------------------------------------------------------------

async def _verify_production_schema() -> None:
    """Production DB yagona Alembic head bilan mosligini tekshiradi."""
    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"

    cfg = AlembicConfig(str(alembic_ini))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    if len(heads) != 1:
        raise RuntimeError(
            "Production migration graph must have exactly one Alembic head"
        )

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        row = result.first()

    current = row[0] if row else "missing"

    if current != heads[0]:
        raise RuntimeError(
            "Database schema is not at the required Alembic head "
            f"(current={current}, required={heads[0]})"
        )


# ---------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Loyiha logging sozlamalarini o'rnatishi mumkin.
    # So'ng terminal handlerini qayta tasdiqlaymiz.
    setup_logging()
    configure_logging()

    logger.info("Starting %s", settings.PROJECT_NAME)
    logger.info("Environment: %s", settings.ENV)

    bot_task = None

    try:
        if settings.AUTO_CREATE_SCHEMA:
            logger.info("Initializing database schema")
            await init_db()
            logger.info("Database schema initialization completed")

        elif settings.is_production:
            logger.info("Verifying production database schema")
            await _verify_production_schema()
            logger.info("Production database schema verified")

        if (
            settings.RUN_TELEGRAM_BOT_IN_API
            and settings.TELEGRAM_BOT_TOKEN
        ):
            from bot import main as run_bot

            bot_task = asyncio.create_task(
                run_bot(),
                name="telegram-bot",
            )
            logger.info("Telegram bot started inside API process")

        logger.info("FastAPI startup completed")

        yield

    except asyncio.CancelledError:
        logger.warning("Application lifespan was cancelled")
        raise

    except Exception:
        logger.exception("Application startup/lifespan error")
        raise

    finally:
        if bot_task is not None:
            bot_task.cancel()

            try:
                await bot_task
            except asyncio.CancelledError:
                logger.info("Telegram bot task cancelled")
            except SystemExit:
                logger.info("Telegram bot stopped")
            except Exception:
                logger.exception("Error while stopping Telegram bot")

        try:
            await engine.dispose()
            logger.info("Database engine disposed")
        except Exception:
            logger.exception("Error while disposing database engine")


# ---------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="3.0.0-mvp",
    description="Mock Center — paper-based mock examination platform API.",
    lifespan=lifespan,
    docs_url=(
        "/docs"
        if settings.ENV.lower() not in ("prod", "production")
        else None
    ),
    redoc_url=(
        "/redoc"
        if settings.ENV.lower() not in ("prod", "production")
        else None
    ),
    openapi_url=(
        "/openapi.json"
        if settings.ENV.lower() not in ("prod", "production")
        else None
    ),
)

app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    rate_limit_error_handler,
)


# ---------------------------------------------------------------------
# Trusted hosts, static files, admin, CORS and sessions
# ---------------------------------------------------------------------

# ASGI test clients use the ``testserver`` Host header. Keep this allowance
# limited to non-production environments; production must use its explicit
# TRUSTED_HOSTS allowlist.
_trusted_hosts = list(settings.TRUSTED_HOSTS or [])
if not settings.is_production and "testserver" not in _trusted_hosts:
    _trusted_hosts.append("testserver")

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_trusted_hosts,
)

os.makedirs(STATIC_DIR, exist_ok=True)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

app.mount("/admin", admin_app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=["*"],
    expose_headers=[
        "Content-Disposition",
        "X-Request-ID",
    ],
)

if settings.SECRET_KEY and settings.SECRET_KEY != "CHANGE_ME_IN_ENV":
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        https_only=settings.COOKIE_SECURE,
        same_site=settings.COOKIE_SAMESITE,
    )


# ---------------------------------------------------------------------
# API routers
# Prefixlar va mavjud endpointlar saqlanadi.
# ---------------------------------------------------------------------

api_routers = (
    auth_router,
    user_router,
    mock_router,
    mock_public_router,
    reading_router,
    admin_reading_router,
    listening_router,
    listening_audio_router,
    admin_listening_router,
    writing_router,
    admin_writing_router,
    speaking_router,

    # Evaluator router catch-all admin routerdan oldin turishi kerak.
    evaluator_speaking_router,
    evaluator_writing_router,

    admin_speaking_router,
    practice_router,
    admin_router,
    billing_router,
    admin_billing_router,
    analysis_router,
    admin_analysis_router,
    exams_auth_router,
    exams_router,
    apply_router,
    answer_sheets_router,
)

for router in api_routers:
    app.include_router(router, prefix="/api/v1")


# ---------------------------------------------------------------------
# Request tracing
# ---------------------------------------------------------------------

def _route_name(request: Request) -> str | None:
    route = request.scope.get("route")
    return getattr(route, "name", None) if route else None


@app.middleware("http")
async def request_id_middleware(
    request: Request,
    call_next,
):
    """
    Har bir HTTP so'rovga server yaratgan request ID beradi.
    Request body, cookie, authorization header yoki API kaliti loglanmaydi.
    """
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id

    started_at = time.perf_counter()
    method = request.method
    path = request.url.path

    logger.info(
        "HTTP IN | request_id=%s | method=%s | path=%s",
        request_id,
        method,
        path,
    )

    try:
        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id

        elapsed_ms = (
            time.perf_counter() - started_at
        ) * 1000

        log_method = (
            logger.warning
            if response.status_code >= 400
            else logger.info
        )

        log_method(
            "HTTP OUT | request_id=%s | method=%s | path=%s "
            "| status=%s | duration_ms=%.2f",
            request_id,
            method,
            path,
            response.status_code,
            elapsed_ms,
        )

        return response

    except Exception:
        elapsed_ms = (
            time.perf_counter() - started_at
        ) * 1000

        logger.exception(
            "HTTP EXCEPTION | request_id=%s | method=%s "
            "| path=%s | duration_ms=%.2f",
            request_id,
            method,
            path,
            elapsed_ms,
        )
        raise


# ---------------------------------------------------------------------
# Admin IP allowlist and login throttling
# ---------------------------------------------------------------------

admin_login_attempts = FailureThrottle(
    "admin-login",
    max_failures=20,
    window_seconds=15 * 60,
)


@app.middleware("http")
async def admin_guard_middleware(
    request: Request,
    call_next,
):
    if request.url.path.startswith("/admin"):
        ip = _get_ip(request)

        if settings.ADMIN_ALLOWED_IPS and (
            ip not in settings.ADMIN_ALLOWED_IPS
        ):
            return JSONResponse(
                status_code=404,
                content={"detail": "Not Found"},
            )

        if (
            request.method == "POST"
            and request.url.path.endswith("/api/sign-in")
        ):
            if admin_login_attempts.is_blocked(ip):
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            "Juda ko'p urinish. "
                            "Keyinroq urinib ko'ring"
                        )
                    },
                )

            admin_login_attempts.record_failure(ip)

    return await call_next(request)


# Cookie-auth so'rovlari uchun mavjud CSRF himoyasi.
app.middleware("http")(csrf_middleware)


# ---------------------------------------------------------------------
# Security response headers
# ---------------------------------------------------------------------

@app.middleware("http")
async def security_headers_middleware(
    request: Request,
    call_next,
):
    response = await call_next(request)

    response.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff",
    )
    response.headers.setdefault(
        "X-Frame-Options",
        "DENY",
    )
    response.headers.setdefault(
        "Referrer-Policy",
        "strict-origin-when-cross-origin",
    )

    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    return response


# ---------------------------------------------------------------------
# Standard error response
# ---------------------------------------------------------------------

def _error_payload(
    *,
    status_code: int,
    code: str,
    message: str,
    fields=None,
    request_id: str | None = None,
):
    error = {
        "status_code": status_code,
        "code": code,
        "message": message,
        "fields": fields,
    }

    if request_id:
        error["request_id"] = request_id

    return {"error": error}


# ---------------------------------------------------------------------
# Application exception handlers
# ---------------------------------------------------------------------

@app.exception_handler(AppError)
async def app_error_handler(
    request: Request,
    exc: AppError,
):
    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            fields=exc.fields,
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    fields: dict[str, list[str]] = {}

    for err in exc.errors():
        loc = ".".join(
            str(part)
            for part in err.get("loc", [])
            if part != "body"
        )

        fields.setdefault(
            loc or "body",
            [],
        ).append(
            err.get("msg", "Noto'g'ri qiymat")
        )

    code = make_code(
        route_name=_route_name(request),
        path=request.url.path,
        status_code=422,
    )

    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    return JSONResponse(
        status_code=422,
        content=_error_payload(
            status_code=422,
            code=code,
            message=(
                "Yuborilgan ma'lumotlarda xatolik bor, "
                "quyidagi maydonlarni tekshiring"
            ),
            fields=fields,
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
):
    route_name = _route_name(request)

    code = make_code(
        route_name=route_name,
        path=request.url.path,
        status_code=exc.status_code,
    )

    if route_name is None and exc.status_code == 404:
        code = "ROUTE_NOT_FOUND__404"

    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    message = (
        exc.detail
        if isinstance(exc.detail, str)
        else "So'rovni bajarib bo'lmadi"
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            status_code=exc.status_code,
            code=code,
            message=message,
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(
    request: Request,
    exc: IntegrityError,
):
    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    logger.exception(
        "Database integrity conflict | request_id=%s | route=%s",
        request_id,
        _route_name(request),
    )

    code = make_code(
        route_name=_route_name(request),
        path=request.url.path,
        status_code=409,
    )

    return JSONResponse(
        status_code=409,
        content=_error_payload(
            status_code=409,
            code=code,
            message=(
                "So'rov mavjud ma'lumotlar yoki "
                "holat bilan to'qnashdi"
            ),
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


@app.exception_handler(OperationalError)
async def operational_db_error_handler(
    request: Request,
    exc: OperationalError,
):
    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    detail = str(
        getattr(exc, "orig", exc)
    ).lower()

    status_code = (
        503
        if "locked" in detail or "busy" in detail
        else 500
    )

    code = (
        "DATABASE_UNAVAILABLE__503"
        if status_code == 503
        else "DATABASE_ERROR__500"
    )

    logger.exception(
        "Database operational error | request_id=%s | route=%s",
        request_id,
        _route_name(request),
    )

    message = (
        "Ma'lumotlar bazasi vaqtincha band. "
        "Birozdan so'ng qayta urinib ko'ring."
        if status_code == 503
        else "Serverda ma'lumotlar bazasi xatosi yuz berdi."
    )

    return JSONResponse(
        status_code=status_code,
        content=_error_payload(
            status_code=status_code,
            code=code,
            message=message,
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(
    request: Request,
    exc: SQLAlchemyError,
):
    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    logger.exception(
        "Unhandled SQLAlchemy error | request_id=%s | route=%s",
        request_id,
        _route_name(request),
    )

    return JSONResponse(
        status_code=500,
        content=_error_payload(
            status_code=500,
            code="DATABASE_ERROR__500",
            message="Serverda ma'lumotlar bazasi xatosi yuz berdi.",
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):
    request_id = getattr(
        request.state,
        "request_id",
        None,
    )

    logger.exception(
        "Unhandled application error | request_id=%s | "
        "method=%s | path=%s | route=%s",
        request_id,
        request.method,
        request.url.path,
        _route_name(request),
    )

    code = make_code(
        route_name=_route_name(request),
        path=request.url.path,
        status_code=500,
    )

    return JSONResponse(
        status_code=500,
        content=_error_payload(
            status_code=500,
            code=code,
            message=(
                "Serverda kutilmagan xatolik yuz berdi. "
                "Birozdan so'ng qayta urinib ko'ring."
            ),
            request_id=request_id,
        ),
        headers=(
            {"X-Request-ID": request_id}
            if request_id
            else None
        ),
    )


# ---------------------------------------------------------------------
# Health and root endpoints
# ---------------------------------------------------------------------

@app.get("/", tags=["System"])
async def root():
    return {
        "message": "Mock Center API is running",
        "version": app.version,
        "docs": "/docs",
    }


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok"}
