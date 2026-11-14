"""Canais de envio dos avisos. Cada canal tem `name`, `configured`, `send(message, key)` e `aclose()`."""
import asyncio
import re
from typing import Awaitable, Callable

import httpx

from app import __version__

USER_AGENT = f"maptiler-usage-monitor/{__version__}"
Sleep = Callable[[float], Awaitable[None]]


class ChannelError(Exception):
    """O envio falhou. A mensagem vai para o histórico: nunca leva chave, telefone ou e-mail."""


def one_line(text: str, limit: int = 200) -> str:
    return " ".join(str(text).split())[:limit]


def redact(text: str, *secrets: str) -> str:
    """Tira chaves e dados pessoais de uma mensagem de erro (inclusive o telefone só com dígitos)."""
    for secret in secrets:
        if not secret:
            continue
        variants = {secret, secret.lstrip("+"), re.sub(r"\D", "", secret)}
        for variant in sorted(variants, key=len, reverse=True):
            if len(variant) >= 4:
                text = text.replace(variant, "…")
    return text


async def request_with_retry(send: Callable[[], Awaitable[httpx.Response]], service: str,
                             sleep: Sleep = asyncio.sleep) -> httpx.Response:
    """Uma nova tentativa, 2 s depois, em falha de rede ou erro temporário (HTTP 429 ou 5xx)."""
    for attempt in (1, 2):
        try:
            response = await send()
        except httpx.TimeoutException:
            problem = f"o {service} não respondeu dentro do tempo-limite"
        except httpx.HTTPError as exc:
            problem = f"falha de rede ao chamar o {service} ({type(exc).__name__})"
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
            problem = f"o {service} respondeu com erro temporário (HTTP {response.status_code})"
        if attempt == 1:
            await sleep(2)
    raise ChannelError(problem)
