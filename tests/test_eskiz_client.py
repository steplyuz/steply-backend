"""Eskiz SMS klienti: token keshi, 401 da qayta login, xato tasnifi, endpointlar.

Haqiqiy tarmoqqa chiqilmaydi: httpx.MockTransport ishlatiladi.
"""
import json

import httpx
import pytest

from app.core.config import settings
from app.modules.auth.sms import OTP_TEMPLATE, SmsApiError, SmsNotConfigured, SmsService


class FakeEskiz:
    """Eskiz API ni taqlid qiladi. token_valid — hozirgi tokenni qabul qiladigan qiymat."""

    def __init__(self, send_status: int = 200):
        self.logins = 0
        self.token = "tok-1"
        self.send_status = send_status
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/auth/login":
            self.logins += 1
            self.token = f"tok-{self.logins}"
            return httpx.Response(200, json={"data": {"token": self.token}})
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Unauthorized"})
        if path == "/api/message/sms/send":
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"message": "bad request body"})
            return httpx.Response(200, json={"id": 555, "status": "waiting"})
        if path.startswith("/api/message/sms/status_by_id/"):
            return httpx.Response(200, json={"status": "success", "data": {"status": "DELIVERED"}})
        if path == "/api/user/prices":
            return httpx.Response(200, json={"global": [], "local": []})
        if path == "/api/report/total-by-dispatch":
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def eskiz(monkeypatch):
    fake = FakeEskiz()
    monkeypatch.setattr(settings, "ESKIZ_EMAIL", "bot@example.com")
    monkeypatch.setattr(settings, "ESKIZ_PASSWORD", "secret")
    monkeypatch.setattr(settings, "RESULT_SMS_SENDER", "4546")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://steply.uz")
    SmsService.invalidate_token()
    SmsService._transport = httpx.MockTransport(fake)
    yield fake
    SmsService._transport = None
    SmsService.invalidate_token()


async def test_send_returns_provider_id_and_caches_token(eskiz):
    provider_id = await SmsService.send_text("+998901234567", "salom")
    assert provider_id == "555"
    await SmsService.send_text("998901234567", "yana")
    assert eskiz.logins == 1, "token keshlanishi kerak"
    sent = [r for r in eskiz.requests if r.url.path == "/api/message/sms/send"]
    body = json.loads(sent[0].content)
    assert body == {"mobile_phone": "998901234567", "message": "salom", "from": "4546"}


async def test_expired_token_is_refreshed_once_on_401(eskiz):
    await SmsService.send_text("998901234567", "1")
    eskiz.token = "tok-revoked-by-eskiz"  # Eskiz tokenni muddatidan oldin bekor qildi
    SmsService._token = "tok-1"  # keshda eski token
    provider_id = await SmsService.send_text("998901234567", "2")
    assert provider_id == "555"
    assert eskiz.logins == 2


async def test_otp_uses_steply_brand_and_template(eskiz):
    await SmsService.send_otp("998901234567", "123456")
    sent = [r for r in eskiz.requests if r.url.path == "/api/message/sms/send"][-1]
    message = json.loads(sent.content)["message"]
    assert message == OTP_TEMPLATE.format(otp="123456")
    assert "Steply" in message and "NarxNav" not in message


async def test_client_error_is_permanent(eskiz):
    eskiz.send_status = 400
    with pytest.raises(SmsApiError) as exc_info:
        await SmsService.send_text("998901234567", "x")
    assert exc_info.value.status_code == 400
    assert exc_info.value.is_permanent
    assert "bad request body" in exc_info.value.message


async def test_server_error_is_transient(eskiz):
    eskiz.send_status = 503
    with pytest.raises(SmsApiError) as exc_info:
        await SmsService.send_text("998901234567", "x")
    assert not exc_info.value.is_permanent


async def test_result_ready_really_sends(eskiz):
    # Oldin await yo'q edi: coroutine hech qachon ishlamagan
    await SmsService.send_result_ready("998901234567")
    sent = [r for r in eskiz.requests if r.url.path == "/api/message/sms/send"]
    assert len(sent) == 1
    assert "https://steply.uz" in json.loads(sent[0].content)["message"]


async def test_status_prices_and_dispatch_report(eskiz):
    status = await SmsService.status_by_id(61359094)
    assert status["data"]["status"] == "DELIVERED"
    prices = await SmsService.prices()
    assert "global" in prices
    report = await SmsService.dispatch_report(123, is_ad=True, status="delivered")
    assert report["status"] == "success"
    req = [r for r in eskiz.requests if r.url.path == "/api/report/total-by-dispatch"][-1]
    assert req.url.params.get("status") == "delivered"
    assert req.content.decode().count("is_ad") == 1


async def test_dispatch_report_rejects_bad_status(eskiz):
    with pytest.raises(ValueError):
        await SmsService.dispatch_report(1, status="weird")


async def test_not_configured_raises(monkeypatch):
    monkeypatch.setattr(settings, "ESKIZ_EMAIL", "")
    monkeypatch.setattr(settings, "ESKIZ_PASSWORD", "")
    SmsService.invalidate_token()
    with pytest.raises(SmsNotConfigured):
        await SmsService.send_text("998901234567", "x")
