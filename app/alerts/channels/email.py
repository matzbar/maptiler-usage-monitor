"""E-mail pelo Resend (https://resend.com/docs/api-reference/emails/send-email).

Sem domínio próprio, o remetente é onboarding@resend.dev e o Resend só entrega no e-mail da própria conta.
"""
import asyncio
import re
from typing import Optional

import httpx

from app.alerts.channels import USER_AGENT, ChannelError, Sleep, one_line, redact, request_with_retry
from app.alerts.messages import Message

RESEND_URL = "https://api.resend.com/emails"


class ResendEmail:
    name = "email"

    def __init__(self, api_key: str, sender: str, recipients: tuple[str, ...], *, timeout_s: float = 10.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None, sleep: Sleep = asyncio.sleep):
        self._key = api_key
        self._sender = sender
        self._recipients = recipients
        self._sleep = sleep
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport, follow_redirects=False,
                                       headers={"User-Agent": USER_AGENT})

    @property
    def configured(self) -> bool:
        return bool(self._key and self._recipients)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, message: Message, key: str) -> None:
        payload = {"from": self._sender, "to": list(self._recipients), "subject": message.subject,
                   "text": message.text}
        headers = {
            "Authorization": f"Bearer {self._key}",
            # Mesma chave nas novas tentativas: se o Resend já tiver enviado, não envia de novo (vale por 24 h).
            "Idempotency-Key": re.sub(r"[^A-Za-z0-9_.-]", "-", key)[:256],
        }
        response = await request_with_retry(
            lambda: self._http.post(RESEND_URL, json=payload, headers=headers), "Resend", self._sleep)
        if 200 <= response.status_code < 300:
            return
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        detail = redact(one_line(detail), self._key, *self._recipients)
        raise ChannelError(f"o Resend recusou o e-mail (HTTP {response.status_code}): {detail}")
