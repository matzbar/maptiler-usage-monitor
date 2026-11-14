"""Cliente da Analytics API do MapTiler (só leitura).

A credencial de serviço dá acesso total à conta do MapTiler, então este módulo:
  - só faz GET, e só na rota de consumo (/v1/analytics/api_usage/timeline), com endereço fixo no código;
  - manda a credencial apenas no cabeçalho Authorization e nunca a registra em log;
  - não segue redirecionamentos (a credencial nunca vai para outro endereço).

Qualquer falha vira AnalyticsError: nunca é lida como consumo zero.
"""
import asyncio
import datetime as dt
import logging
import random
import time
from typing import Any, Awaitable, Callable, Literal, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app import __version__

ANALYTICS_URL = "https://service.maptiler.com/v1/analytics/api_usage/timeline"
PERIOD = "current_billing_period"
USER_AGENT = f"maptiler-usage-monitor/{__version__}"
MAX_RETRY_AFTER_S = 30.0  # teto para a espera pedida pelo MapTiler num HTTP 429

Classifier = Literal["services", "api_keys"]
Group = Literal["request", "session"]

logger = logging.getLogger("monitor.maptiler")


# Formato da resposta, conferido em 14/11/2026 (https://docs.maptiler.com/cloud/admin-api/analytics/).
# Campos a mais são ignorados; campos que faltam ou com tipo errado tornam a resposta inválida.
class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Point(_Model):
    date: dt.date
    value: int = Field(ge=0)


class Dataset(_Model):
    group_id: str
    item_id: Optional[str] = None
    data: list[Point]
    estimated_data: Any = None  # sem formato documentado; não entra nas contas


class LegendItem(_Model):
    item_id: str
    label: Optional[str] = None  # serviço: nome legível; chave: a própria chave pública
    description: Optional[str] = None  # chave: o nome dado a ela no MapTiler


class Timeline(_Model):
    since: dt.date
    until: dt.date
    legend: list[LegendItem] = []
    datasets: list[Dataset] = []

    @model_validator(mode="after")
    def _period_in_order(self) -> "Timeline":
        if self.since > self.until:
            raise ValueError("since é posterior a until")
        return self


class AnalyticsError(Exception):
    """Falha ao obter o consumo.

    kind: not_configured, unauthorized, rejected, rate_limited, unavailable ou invalid_response.
    """

    def __init__(self, kind: str, message: str, http_status: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.http_status = http_status


def _error_text(response: httpx.Response) -> str:
    """Mensagem de erro do MapTiler, curta e numa linha só."""
    try:
        errors = response.json().get("errors") or []
        text = errors[0].get("message") or errors[0].get("msg") or ""
    except Exception:
        text = response.text
    return " ".join(str(text).split())[:200]


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(float(response.headers.get("retry-after", "0")), MAX_RETRY_AFTER_S)
    except ValueError:
        return 0.0


class AnalyticsClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_s: float = 10.0,
        attempts: int = 3,
        retry_base_s: float = 1.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._token = token
        self._attempts = max(1, attempts)
        self._retry_base_s = retry_base_s
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def timeline(self, classifier: Classifier, group: Group) -> Timeline:
        """Consumo do período de cobrança atual, dia a dia, de um grupo (request ou session)."""
        if not self._token:
            raise AnalyticsError("not_configured", "MAPTILER_SERVICE_TOKEN não está definido")
        params = {"period": PERIOD, "classifier": classifier, "group": group}
        log = {"event": "analytics_request", "classifier": classifier, "group": group}
        error = AnalyticsError("unavailable", "nenhuma tentativa feita")
        for attempt in range(1, self._attempts + 1):
            started = time.monotonic()
            # Espera antes da próxima tentativa: 1 s, 2 s, 4 s... com até 25% a mais, para não sincronizar.
            wait = self._retry_base_s * 2 ** (attempt - 1) * (1 + random.random() / 4)
            status: Optional[int] = None
            try:
                response = await self._http.get(
                    ANALYTICS_URL, params=params, headers={"Authorization": f"Token {self._token}"}
                )
            except httpx.TimeoutException:
                error = AnalyticsError("unavailable", "o MapTiler não respondeu dentro do tempo-limite")
            except httpx.HTTPError as exc:
                error = AnalyticsError("unavailable", f"falha de rede ao consultar o MapTiler ({type(exc).__name__})")
            else:
                status = response.status_code
                duration_ms = round((time.monotonic() - started) * 1000)
                logger.info("consulta à Analytics API", extra={**log, "attempt": attempt, "http_status": status,
                                                                 "duration_ms": duration_ms})
                if status == 200:
                    return self._parse(response, group)
                if status in (401, 403):
                    raise AnalyticsError("unauthorized", "o MapTiler recusou a credencial de serviço", status)
                if status == 429:
                    error = AnalyticsError("rate_limited", "o MapTiler pediu para esperar (HTTP 429)", status)
                    wait = max(wait, _retry_after(response))
                elif status >= 500:
                    error = AnalyticsError("unavailable", f"o MapTiler respondeu com erro (HTTP {status})", status)
                else:
                    raise AnalyticsError(
                        "rejected", f"o MapTiler recusou a consulta (HTTP {status}): {_error_text(response)}", status
                    )
            last = attempt == self._attempts
            logger.warning(
                "falha temporária na Analytics API" + ("" if last else "; nova tentativa em breve"),
                extra={**log, "attempt": attempt, "kind": error.kind, "http_status": status,
                       "retry_in_s": None if last else round(wait, 1)},
            )
            if not last:
                await self._sleep(wait)
        raise error

    @staticmethod
    def _parse(response: httpx.Response, group: str) -> Timeline:
        try:
            payload = response.json()
        except ValueError:
            raise AnalyticsError("invalid_response", "a resposta do MapTiler não é um JSON válido", 200) from None
        try:
            timeline = Timeline.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "raiz"
            raise AnalyticsError(
                "invalid_response", f"resposta do MapTiler em formato inesperado ({where}: {first['msg']})", 200
            ) from None
        # Um grupo diferente do pedido indica mudança na API: melhor falhar do que somar errado (ou contar zero).
        unexpected = {d.group_id for d in timeline.datasets} - {group}
        if unexpected:
            raise AnalyticsError(
                "invalid_response", f"o MapTiler devolveu o grupo {sorted(unexpected)} ao ser pedido {group!r}", 200
            )
        return timeline
