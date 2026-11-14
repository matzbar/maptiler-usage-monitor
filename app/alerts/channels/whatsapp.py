"""WhatsApp pelo CallMeBot (https://www.callmebot.com/blog/free-api-whatsapp-messages/).

Gratuito para uso pessoal: as mensagens chegam só no número que ativou a chave. A API recebe telefone, texto e
chave na própria URL, então essa URL nunca é registrada em log.
"""
import asyncio
import logging
import re
from typing import Optional

import httpx

from app.alerts.channels import USER_AGENT, ChannelError, Sleep, one_line, redact, request_with_retry
from app.alerts.messages import Message

CALLMEBOT_HOST = "api.callmebot.com"
CALLMEBOT_URL = f"https://{CALLMEBOT_HOST}/whatsapp.php"


class _HideCallMeBotRequests(logging.Filter):
    """O httpx registra a URL de cada requisição; na do CallMeBot vão a chave e o telefone. Nunca registrar."""

    def filter(self, record: logging.LogRecord) -> bool:
        return CALLMEBOT_HOST not in record.getMessage()


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, _HideCallMeBotRequests) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(_HideCallMeBotRequests())


class CallMeBotWhatsApp:
    name = "whatsapp"

    def __init__(self, phone: str, api_key: str, *, timeout_s: float = 15.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None, sleep: Sleep = asyncio.sleep):
        self._phone = phone
        self._key = api_key
        self._sleep = sleep
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport, follow_redirects=False,
                                       headers={"User-Agent": USER_AGENT})

    @property
    def configured(self) -> bool:
        return bool(self._phone and self._key)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, message: Message, key: str) -> None:
        params = {"phone": self._phone, "text": message.short, "apikey": self._key}
        response = await request_with_retry(lambda: self._http.get(CALLMEBOT_URL, params=params), "CallMeBot",
                                            self._sleep)
        # A resposta é uma página HTML; o envio aceito diz que a mensagem entrou na fila ("queued").
        page = one_line(re.sub(r"<[^>]+>", " ", response.text), 300)
        if response.status_code == 200 and re.search(r"\bqueued\b", page, re.I) and not re.search(
                r"\b(invalid|error)\b", page, re.I):
            return
        detail = redact(page, self._key, self._phone)[:160]
        raise ChannelError(f"o CallMeBot não confirmou o envio (HTTP {response.status_code}): {detail}")
