import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.internal_auth import verify_internal_token
from app.core.rate_limit import limiter
from app.modules.auth.dependencies import get_current_user, get_current_user_allow_forced_change
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    PhoneLoginRequest, PhoneAuthResponse, StatusResponse, RegisterRequest,
    RegisterInitResponse, RegisterVerifyRequest, RegisterResponse,
    ForgotPasswordRequest, ResetPasswordRequest, Token, PasswordChangeRequest,
    TelegramRegisterStartRequest, TelegramRegisterStartResponse,
    TelegramRegisterContactRequest, TelegramRegisterContactResponse,
    TelegramRegisterCodeRequest, TelegramRegisterCodeResponse,
    TelegramVerifyRequest, GoogleVerifyRequest,
)
from app.modules.auth.service import AuthService
from app.modules.auth.google_oidc import GoogleOIDCService
from app.modules.auth.telegram_oidc import TelegramOIDCService
from app.modules.users.schemas import UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger(__name__)


def get_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)


def set_auth_cookies(response: Response, token: Token) -> None:
    cookie_kwargs = dict(
        httponly=settings.COOKIE_HTTPONLY,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    if settings.COOKIE_DOMAIN:
        cookie_kwargs["domain"] = settings.COOKIE_DOMAIN
    response.set_cookie("access_token", token.access_token, max_age=settings.ACCESS_TOKEN_MINUTES * 60, **cookie_kwargs)
    response.set_cookie("refresh_token", token.refresh_token, max_age=settings.REFRESH_TOKEN_DAYS * 86400, **cookie_kwargs)


def clear_auth_cookies(response: Response) -> None:
    kwargs = {"path": "/"}
    if settings.COOKIE_DOMAIN:
        kwargs["domain"] = settings.COOKIE_DOMAIN
    response.delete_cookie("access_token", **kwargs)
    response.delete_cookie("refresh_token", **kwargs)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/sms-registration/status")
async def sms_registration_status(db: AsyncSession = Depends(get_db)):
    from app.modules.admin.system_settings import SystemSettingsService
    return {"sms_registration_enabled": await SystemSettingsService.sms_registration_enabled(db)}


@router.post("/refresh", response_model=StatusResponse)
@limiter.limit("10/minute")
async def refresh(request: Request, response: Response, service: AuthService = Depends(get_service)):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(401, "Refresh token topilmadi")
    token = await service.refresh(refresh_token, request)
    set_auth_cookies(response, token)
    return StatusResponse(status="success", message="Token yangilandi")


# -----------------------------------------------------------------------------
# Telegram OIDC — the only direct Telegram login flow
# -----------------------------------------------------------------------------

@router.get("/telegram")
@limiter.limit("10/minute")
async def telegram_oidc_login(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    authorization_url = await TelegramOIDCService(db).start(response)
    return RedirectResponse(url=authorization_url, status_code=302, headers=dict(response.headers))


@router.post("/telegram/nonce")
@limiter.limit("20/minute")
async def telegram_login_nonce(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Popup flow, step 1: one-time nonce for telegram-login.js (+ public client_id)."""
    return await TelegramOIDCService(db).start_popup(response, request)


@router.post("/telegram/verify", response_model=StatusResponse)
@limiter.limit("10/minute")
async def telegram_login_verify(
    request: Request,
    response: Response,
    data: TelegramVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Popup flow, step 2: validate the id_token and open our own cookie session."""
    token = await TelegramOIDCService(db).verify_popup(
        request=request, response=response, id_token=data.id_token
    )
    set_auth_cookies(response, token)
    return StatusResponse(status="success", message="Telegram orqali kirildi")


# -----------------------------------------------------------------------------
# Google Sign-In (Google Identity Services, id_token JS'dan keladi)
# -----------------------------------------------------------------------------

@router.post("/google/nonce")
@limiter.limit("20/minute")
async def google_login_nonce(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Google, 1-qadam: bir martalik nonce (+ public client_id)."""
    return await GoogleOIDCService(db).start(response, request)


@router.post("/google/verify", response_model=StatusResponse)
@limiter.limit("10/minute")
async def google_login_verify(
    request: Request, response: Response, data: GoogleVerifyRequest, db: AsyncSession = Depends(get_db),
):
    """Google, 2-qadam: id_token'ni tekshirib, o'z cookie sessiyamizni ochadi."""
    token = await GoogleOIDCService(db).verify(request=request, response=response, id_token=data.id_token)
    set_auth_cookies(response, token)
    return StatusResponse(status="success", message="Google orqali kirildi")


@router.get("/telegram/callback")
@limiter.limit("20/minute")
async def telegram_oidc_callback(
    request: Request,
    response: Response,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if error or not code or not state:
        raise HTTPException(400, "Telegram authentication failed")

    token = await TelegramOIDCService(db).callback(request=request, response=response, code=code, state=state)
    redirect = RedirectResponse(
        url=settings.FRONTEND_URL.rstrip("/") + "/telegram/callback?status=success",
        status_code=303,
    )
    set_auth_cookies(redirect, token)
    redirect.delete_cookie("telegram_oidc_browser", path="/api/v1/auth/telegram", domain=settings.COOKIE_DOMAIN)
    return redirect


# -----------------------------------------------------------------------------
# Registration — phone verification code is sent by SMS and can be retrieved
# through the user's Telegram account after the deep-link + contact check.
# -----------------------------------------------------------------------------

@router.post("/register", response_model=RegisterInitResponse)
@limiter.limit("5/minute")
async def register(request: Request, data: RegisterRequest, service: AuthService = Depends(get_service)):
    return await service.register(data, request)


@router.post("/register/verify", response_model=RegisterResponse)
@limiter.limit("10/minute")
async def register_verify(
    request: Request,
    data: RegisterVerifyRequest,
    response: Response,
    service: AuthService = Depends(get_service),
):
    result = await service.register_verify(data.phone, data.code, request)
    if result.status == "success" and result.token:
        set_auth_cookies(response, result.token)
    return result


# Internal bot contract. The bot is the only caller allowed to these endpoints.
@router.post("/telegram/register/start", response_model=TelegramRegisterStartResponse, dependencies=[Depends(verify_internal_token)])
async def telegram_register_start(data: TelegramRegisterStartRequest, service: AuthService = Depends(get_service)):
    return await service.telegram_register_start(
        start_token=data.start_token,
        telegram_user_id=data.telegram_user_id,
        chat_id=data.chat_id,
        username=data.username,
        first_name=data.first_name,
        last_name=data.last_name,
        photo_url=data.photo_url,
    )


@router.post("/telegram/register/contact", response_model=TelegramRegisterContactResponse, dependencies=[Depends(verify_internal_token)])
async def telegram_register_contact(data: TelegramRegisterContactRequest, service: AuthService = Depends(get_service)):
    return await service.telegram_register_verify_contact(
        start_token=data.start_token,
        telegram_user_id=data.telegram_user_id,
        chat_id=data.chat_id,
        phone=data.phone,
    )


@router.post("/telegram/register/code", response_model=TelegramRegisterCodeResponse, dependencies=[Depends(verify_internal_token)])
async def telegram_register_code(data: TelegramRegisterCodeRequest, service: AuthService = Depends(get_service)):
    code = await service.telegram_register_get_code(
        start_token=data.start_token,
        telegram_user_id=data.telegram_user_id,
    )
    return TelegramRegisterCodeResponse(status="ready", code=code)


@router.post("/phone/login", response_model=PhoneAuthResponse)
@limiter.limit("5/minute")
async def phone_login(request: Request, data: PhoneLoginRequest, response: Response, service: AuthService = Depends(get_service)):
    result = await service.authenticate_by_phone(phone=data.phone, password=data.password, request=request)
    if result.status == "success" and result.token:
        set_auth_cookies(response, result.token)
    return result


@router.post("/password/change", response_model=StatusResponse)
async def change_password(
    data: PasswordChangeRequest,
    current_user: User = Depends(get_current_user_allow_forced_change),
    service: AuthService = Depends(get_service),
):
    await service.change_password(current_user.id, data.current_password, data.new_password)
    return StatusResponse(status="success", message="Parol muvaffaqiyatli o'zgartirildi")


@router.post("/password/forgot", response_model=StatusResponse)
@limiter.limit("5/minute")
async def forgot_password(request: Request, data: ForgotPasswordRequest, service: AuthService = Depends(get_service)):
    await service.forgot_password(data.phone, request)
    return StatusResponse(status="success", message="Parolni tiklash kodi yuborildi")


@router.post("/password/reset", response_model=StatusResponse)
@limiter.limit("10/minute")
async def reset_password(request: Request, data: ResetPasswordRequest, service: AuthService = Depends(get_service)):
    return await service.reset_password(data.phone, data.code, data.new_password)


@router.post("/logout", response_model=StatusResponse)
async def logout(request: Request, response: Response, service: AuthService = Depends(get_service)):
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        await service.logout(refresh_token)
    clear_auth_cookies(response)
    return StatusResponse(status="success", message="Chiqildi")
