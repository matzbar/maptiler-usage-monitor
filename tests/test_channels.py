import json
import logging

import httpx
import pytest

from app.alerts.channels import ChannelError, redact
from app.alerts.channels.email import ResendEmail
from app.alerts.channels.whatsapp import CallMeBotWhatsApp
from app.alerts.messages import Message

pytestmark = pytest.mark.anyio

RESEND_KEY = "re_chave_ficticia_123456"
CALLMEBOT_KEY = "9876543"
PHONE = "+5511912345678"
EMAIL = "pessoa@example.com"
MESSAGE = Message("Assunto", "Corpo do e-mail", "Mensagem curta")


def transport(responses, calls):
    def handler(request):
        calls.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return httpx.MockTransport(handler)


async def no_sleep(_seconds):
    return None


def email_channel(responses, calls, **kw):
    return ResendEmail(kw.get("key", RESEND_KEY), "MapTiler Monitor <onboarding@resend.dev>", (EMAIL,),
                       transport=transport(responses, calls), sleep=no_sleep)


def whatsapp_channel(responses, calls):
    return CallMeBotWhatsApp(PHONE, CALLMEBOT_KEY, transport=transport(responses, calls), sleep=no_sleep)


async def test_resend_request():
    calls = []
    ch = email_channel([httpx.Response(200, json={"id": "abc"})], calls)
    await ch.send(MESSAGE, "2026-11-14:requests:80:email")
    await ch.aclose()
    r = calls[0]
    assert r.method == "POST" and str(r.url) == "https://api.resend.com/emails"
    assert r.headers["authorization"] == f"Bearer {RESEND_KEY}"
    assert r.headers["idempotency-key"] == "2026-11-14-requests-80-email"
    assert json.loads(r.content) == {"from": "MapTiler Monitor <onboarding@resend.dev>", "to": [EMAIL],
                                     "subject": "Assunto", "text": "Corpo do e-mail"}


async def test_resend_refusal_is_reported_without_secrets():
    calls = []
    body = {"statusCode": 403, "name": "validation_error",
            "message": f"You can only send testing emails to your own email address ({EMAIL})."}
    ch = email_channel([httpx.Response(403, json=body)], calls)
    with pytest.raises(ChannelError) as exc:
        await ch.send(MESSAGE, "k")
    await ch.aclose()
    assert "HTTP 403" in str(exc.value) and "only send testing emails" in str(exc.value)
    assert EMAIL not in str(exc.value) and RESEND_KEY not in str(exc.value) and len(calls) == 1


async def test_resend_temporary_error_is_retried_once():
    calls = []
    ch = email_channel([httpx.Response(503), httpx.Response(200, json={"id": "abc"})], calls)
    await ch.send(MESSAGE, "k")
    assert len(calls) == 2
    calls.clear()
    ch2 = email_channel([httpx.ReadTimeout("lento"), httpx.ReadTimeout("lento")], calls)
    with pytest.raises(ChannelError, match="tempo-limite"):
        await ch2.send(MESSAGE, "k")
    await ch.aclose()
    await ch2.aclose()
    assert len(calls) == 2


async def test_email_not_configured_without_key_or_recipient():
    assert not ResendEmail("", "x <a@b.c>", (EMAIL,)).configured
    assert not ResendEmail(RESEND_KEY, "x <a@b.c>", ()).configured


async def test_callmebot_request_and_success():
    calls = []
    page = "<html><body><p>Message to: +5511912345678<br>Text: Mensagem curta<br>Message queued. You will " \
           "receive it in a few seconds.</p></body></html>"
    ch = whatsapp_channel([httpx.Response(200, text=page)], calls)
    await ch.send(MESSAGE, "k")
    await ch.aclose()
    r = calls[0]
    assert r.method == "GET" and r.url.host == "api.callmebot.com" and r.url.path == "/whatsapp.php"
    assert dict(r.url.params) == {"phone": PHONE, "text": "Mensagem curta", "apikey": CALLMEBOT_KEY}


@pytest.mark.parametrize("status,page", [
    (203, "<b>APIKey is invalid.</b> Please make sure you are using the right apikey for +5511912345678"),
    (200, "<html>Error: phone not activated</html>"),
    (200, "<html>algo inesperado</html>"),
])
async def test_callmebot_failure_hides_phone_and_key(status, page):
    calls = []
    ch = whatsapp_channel([httpx.Response(status, text=page)], calls)
    with pytest.raises(ChannelError) as exc:
        await ch.send(MESSAGE, "k")
    await ch.aclose()
    text = str(exc.value)
    assert f"HTTP {status}" in text
    assert "5511912345678" not in text and CALLMEBOT_KEY not in text


async def test_channel_errors_and_urls_stay_out_of_logs(caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    ch = whatsapp_channel([httpx.Response(200, text="Message queued.")], calls)
    await ch.send(MESSAGE, "k")
    await ch.aclose()
    assert CALLMEBOT_KEY not in caplog.text and "5511912345678" not in caplog.text


def test_redact():
    assert redact("tel 5511912345678 e +5511912345678", "+5511912345678") == "tel … e …"
    assert redact("chave abc", "") == "chave abc"
