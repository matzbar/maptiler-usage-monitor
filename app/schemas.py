"""Formato das respostas da API (aparece na documentação em /docs e no /openapi.json)."""
import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, Field

Level = Literal["normal", "notice", "warning", "high", "very_high", "critical"]
LEVEL_HELP = ("Faixa de consumo pela maior porcentagem entre requests e sessions: normal (< 50%), notice (50%), "
              "warning (70%), high (80%), very_high (90%), critical (100%).")


class Period(BaseModel):
    since: dt.date = Field(description="Primeiro dia do período de cobrança")
    until: dt.date = Field(description="Último dia do período de cobrança")


class Metric(BaseModel):
    used: int = Field(description="Consumo no período")
    quota: int = Field(description="Cota do período (configurável)")
    percent: float = Field(description="Porcentagem da cota usada, truncada em 1 casa decimal")


class Projection(BaseModel):
    requests: int = Field(description="Requests previstos até o fim do período, pela média diária")
    requests_percent: float
    sessions: int = Field(description="Sessions previstas até o fim do período, pela média diária")
    sessions_percent: float
    based_on_days: int = Field(description="Dias do período já passados (com poucos dias, a projeção oscila)")
    days_in_period: int


class KeyUsage(BaseModel):
    key: str = Field(description="Chave do MapTiler, mascarada")
    name: Optional[str] = Field(description="Nome dado à chave no MapTiler")
    requests: int
    sessions: int


class ServiceUsage(BaseModel):
    service: str = Field(description="Identificador do serviço no MapTiler")
    label: Optional[str] = Field(description="Nome do serviço")
    requests: int
    sessions: int


class LastError(BaseModel):
    checked_at: dt.datetime
    kind: str = Field(description="not_configured, unauthorized, rejected, rate_limited, unavailable, "
                                  "invalid_response ou internal")
    message: str


class UsageResponse(BaseModel):
    status: Literal["ok", "stale", "unavailable"] = Field(
        description="ok: dado recente; stale: dado antigo ou de um período já encerrado; unavailable: nenhuma "
                    "verificação deu certo ainda (sem números, nunca zero por falha)")
    checked_at: Optional[dt.datetime] = Field(description="Quando o consumo mostrado foi obtido do MapTiler")
    period: Optional[Period] = None
    requests: Optional[Metric] = None
    sessions: Optional[Metric] = None
    level: Optional[Level] = Field(None, description=LEVEL_HELP)
    projection: Optional[Projection] = None
    keys: list[KeyUsage] = Field([], description="Consumo das chaves do MapTiler deste projeto")
    services: list[ServiceUsage] = Field([], description="Consumo da conta por serviço do MapTiler")
    last_error: Optional[LastError] = Field(None, description="Falha mais recente, se houve depois do dado mostrado")


class DailyUsage(BaseModel):
    date: dt.date
    requests: int
    sessions: int


class HistoryResponse(BaseModel):
    days: int
    daily: list[DailyUsage]


class CheckError(BaseModel):
    kind: str
    message: str
    http_status: Optional[int] = None


NotificationStatus = Literal["recorded", "sending", "sent", "failed", "skipped"]
STATUS_HELP = ("recorded: só registrado (aparece no painel); sending: enviando; sent: enviado; failed: falhou "
               "(nova tentativa nas próximas verificações); skipped: não enviado (superado por uma faixa mais alta "
               "ou canal não configurado)")


class NotificationSummary(BaseModel):
    kind: Literal["usage", "monitor_down", "test", "report"]
    metric: Optional[Literal["requests", "sessions"]] = None
    threshold: Optional[int] = Field(None, description="Faixa, em % da cota")
    channel: Literal["panel", "email", "whatsapp"]
    status: NotificationStatus = Field(description=STATUS_HELP)


class CheckResponse(BaseModel):
    status: Literal["ok", "failed"]
    checked_at: dt.datetime
    duration_ms: int
    trigger: str = Field(description="schedule (agendador), manual (administrador) ou on_demand (leitura)")
    period: Optional[Period] = None
    requests: Optional[Metric] = None
    sessions: Optional[Metric] = None
    level: Optional[Level] = Field(None, description=LEVEL_HELP)
    period_changed: Optional[bool] = Field(None, description="true na primeira verificação de um novo período")
    error: Optional[CheckError] = None
    notifications: list[NotificationSummary] = Field([], description="Avisos criados ou reenviados nesta verificação")


class AlertRecord(NotificationSummary):
    id: str
    period: Optional[Period] = None
    percent: Optional[float] = Field(None, description="Porcentagem da cota quando o aviso foi criado")
    used: Optional[int] = None
    quota: Optional[int] = None
    attempts: int
    created_at: dt.datetime
    sent_at: Optional[dt.datetime] = None
    last_error: Optional[str] = None


class AlertsResponse(BaseModel):
    alerts: list[AlertRecord] = Field(description="Do mais recente para o mais antigo")


class ChecksResponse(BaseModel):
    checks: list[CheckResponse] = Field(description="Da mais recente para a mais antiga")


class ReportResponse(BaseModel):
    status: Literal["sent", "failed", "already_sent", "not_configured", "sending"] = Field(
        description="already_sent: o relatório deste dia já foi enviado; not_configured: e-mail não configurado")
    date: dt.date = Field(description="Dia do relatório (no fuso ALERT_TIMEZONE)")
    error: Optional[str] = None


class TestResult(BaseModel):
    channel: Literal["email", "whatsapp"]
    status: Literal["sent", "failed", "not_configured", "sending"]
    error: Optional[str] = None


class TestAlertResponse(BaseModel):
    results: list[TestResult]


class TooSoonResponse(BaseModel):
    detail: str
    retry_after_s: int
    last_check: CheckResponse


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    commit: Optional[str] = None
