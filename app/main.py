"""API do monitor de consumo do MapTiler.

Rodar: uvicorn app.main:create_app --factory
"""
import datetime as dt
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from app import __version__
from app.alerts.channels.email import ResendEmail
from app.alerts.channels.whatsapp import CallMeBotWhatsApp
from app.alerts.service import AlertService
from app.auth import Principal, identify
from app.config import Settings, load_settings
from app.logs import setup_logging
from app.maptiler import AnalyticsClient
from app.monitoring.check import Monitor, utcnow
from app.ratelimit import RateLimiter, client_ip
from app.monitoring.engine import mask_key
from app.schemas import (AlertRecord, AlertsResponse, CheckResponse, ChecksResponse, ClientsResponse,
                         ClientSummary, HealthResponse, HistoryResponse, ReportResponse, TestAlertResponse, TooSoonResponse, UsageResponse)
from app.security import headers_for
from app.storage import MemoryStorage, MongoStorage, Storage

TEST_ALERT_INTERVAL_S = 60  # no máximo um teste de aviso por minuto
PANEL_DIR = Path(__file__).parent / "panel"
PANEL_STATIC_PREFIX = "/panel/static/"

logger = logging.getLogger("monitor.api")

DESCRIPTION = """
Acompanha o consumo de uma conta [MapTiler](https://www.maptiler.com/cloud/) pela
[Analytics API](https://docs.maptiler.com/cloud/admin-api/analytics/): requests e sessions do período de
cobrança, porcentagem da cota, faixa de consumo, projeção e histórico.

**Autenticação:** `Authorization: Bearer <token>`. Projetos clientes usam a própria chave (criada com
`python -m scripts.clients create`); o agendador usa o `CHECK_TOKEN`; o administrador, o `ADMIN_TOKEN`.

A credencial de serviço do MapTiler fica só neste servidor e nunca aparece nas respostas.
"""

bearer = HTTPBearer(auto_error=False, description="Chave do projeto, CHECK_TOKEN ou ADMIN_TOKEN")


def check_summary(record: dict) -> CheckResponse:
    fields = ("status", "checked_at", "duration_ms", "trigger", "period", "requests", "sessions", "level",
              "period_changed", "error")
    return CheckResponse(**{f: record.get(f) for f in fields}, notifications=record.get("notifications") or [])


def alert_record(doc: dict) -> AlertRecord:
    fields = ("kind", "metric", "threshold", "channel", "status", "period", "percent", "used", "quota", "attempts",
              "created_at", "sent_at", "last_error")
    return AlertRecord(id=doc["_id"], **{f: doc.get(f) for f in fields})


def create_app(
    settings: Optional[Settings] = None,
    storage: Optional[Storage] = None,
    analytics=None,
    clock: Callable[[], dt.datetime] = utcnow,
    configure_logging: bool = True,
    channels: Optional[dict] = None,
) -> FastAPI:
    """Monta a API. Os parâmetros servem aos testes; em produção tudo vem das variáveis de ambiente."""
    settings = settings or load_settings()
    if configure_logging:
        setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = storage
        if st is None:
            if settings.mongo_url:
                st = MongoStorage(settings.mongo_url, settings.db_name, settings.retention_days)
            else:
                logger.warning("MONGO_URL não definido: o histórico fica só na memória e some ao reiniciar",
                               extra={"event": "memory_storage"})
                st = MemoryStorage()
        try:
            await st.connect()
        except PyMongoError as exc:
            # Sobe mesmo assim: /health responde e as rotas de dados devolvem 503 até o banco voltar.
            logger.error("banco indisponível na inicialização", extra={"event": "storage_unavailable",
                                                                       "error": type(exc).__name__})
        an = analytics or AnalyticsClient(settings.maptiler_service_token, timeout_s=settings.http_timeout_s,
                                          attempts=settings.http_attempts, retry_base_s=settings.retry_base_s)
        if not an.configured:
            logger.warning("MAPTILER_SERVICE_TOKEN não definido: as verificações vão falhar",
                           extra={"event": "token_missing"})
        chans = channels if channels is not None else {
            "email": ResendEmail(settings.resend_api_key, settings.alert_email_from, settings.alert_email_to),
            "whatsapp": CallMeBotWhatsApp(settings.alert_whatsapp_phone, settings.callmebot_api_key),
        }
        alerts = AlertService(settings, st, chans, clock)
        monitor = Monitor(settings, st, an, clock, alerts)
        app.state.storage, app.state.monitor, app.state.alerts = st, monitor, alerts
        app.state.last_test_alert = None
        logger.info("monitor iniciado", extra={
            "event": "startup", "version": __version__, "storage": type(st).__name__, "commit": settings.build_commit,
            "channels": sorted(name for name, ch in chans.items() if ch.configured)})
        try:
            yield
        finally:
            await monitor.aclose()
            if channels is None:
                await alerts.aclose()
            if analytics is None:
                await an.aclose()
            if storage is None:
                await st.close()

    app = FastAPI(title="MapTiler Usage Monitor", version=__version__, description=DESCRIPTION,
                  lifespan=lifespan, redoc_url=None)
    # Comprime respostas a partir de 1 KB (JS, CSS e JSON do painel). As respostas não refletem dados enviados
    # por terceiros junto com segredos, então não há risco do tipo BREACH.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    limiter = RateLimiter(settings.rate_limit_per_min)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        response = None
        # Fora do limite: o health check (Render e Docker) e os arquivos estáticos do painel (sem dados nem segredos;
        # cada abertura da página baixa uns 15 deles e, contando, duas aberturas seguidas já estourariam o limite).
        if path != "/health" and not path.startswith(PANEL_STATIC_PREFIX):
            ip = client_ip(request.headers, request.client.host if request.client else None,
                           settings.trusted_proxy_hops, settings.client_ip_header)
            wait = limiter.hit(ip)
            if wait is not None:
                response = JSONResponse({"detail": "muitas requisições; tente de novo em instantes"},
                                        status_code=429, headers={"Retry-After": str(int(wait) + 1)})
        if response is None:
            response = await call_next(request)
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        for name, value in headers_for(path, proto).items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(PyMongoError)
    async def storage_down(_request: Request, exc: PyMongoError):
        logger.error("falha no banco", extra={"event": "storage_error", "error": type(exc).__name__})
        return JSONResponse({"detail": "banco de dados indisponível; tente de novo em instantes"}, status_code=503)

    async def principal(request: Request,
                        creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Principal:
        who = await identify(creds.credentials if creds else "", settings, request.app.state.storage)
        if who is None:
            raise HTTPException(401, "token ausente ou inválido", headers={"WWW-Authenticate": "Bearer"})
        return who

    def require(*roles: str):
        async def dependency(who: Principal = Depends(principal)) -> Principal:
            if who.role not in roles:
                raise HTTPException(403, "este token não dá acesso a esta rota")
            return who
        return dependency

    @app.get("/", include_in_schema=False)
    async def root():
        return {"name": "maptiler-usage-monitor", "version": __version__, "docs": "/docs", "panel": "/panel"}

    # Painel do administrador: página estática (sem dados). Os dados vêm das rotas /v1, com o ADMIN_TOKEN que a
    # pessoa digita na página e que fica só na aba do navegador (sessionStorage).
    app.mount(PANEL_STATIC_PREFIX.rstrip("/"), StaticFiles(directory=PANEL_DIR / "static"), name="panel-static")

    @app.get("/panel", include_in_schema=False)
    async def panel():
        return FileResponse(PANEL_DIR / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/health", response_model=HealthResponse, tags=["saúde"], summary="A API está no ar?")
    async def health():
        return HealthResponse(status="ok", version=__version__, commit=settings.build_commit)

    @app.post(
        "/v1/checks", response_model=CheckResponse, tags=["verificação"],
        summary="Consulta o MapTiler agora e grava o resultado",
        description="Usado pelo agendador (CHECK_TOKEN) e pelo administrador. HTTP 502 quando a consulta ao "
                    "MapTiler falha (a falha fica gravada); 429 se a última verificação foi há menos de "
                    "CHECK_MIN_INTERVAL_MIN minutos; 409 se já há uma em andamento.",
        responses={502: {"model": CheckResponse, "description": "A consulta ao MapTiler falhou"},
                   429: {"model": TooSoonResponse, "description": "Verificação recente demais"},
                   409: {"description": "Verificação em andamento"}},
    )
    async def run_check(request: Request, who: Principal = Depends(require("scheduler", "admin"))):
        result = await request.app.state.monitor.check_now("schedule" if who.role == "scheduler" else "manual")
        if result.outcome == "busy":
            raise HTTPException(409, "já há uma verificação em andamento")
        if result.outcome == "too_soon":
            body = TooSoonResponse(detail="a última verificação é recente; espere para verificar de novo",
                                   retry_after_s=result.retry_after_s, last_check=check_summary(result.record))
            return JSONResponse(jsonable_encoder(body), status_code=429,
                                headers={"Retry-After": str(result.retry_after_s)})
        summary = check_summary(result.record)
        if summary.status == "failed":
            return JSONResponse(jsonable_encoder(summary), status_code=502)
        return summary

    @app.get("/v1/usage", response_model=UsageResponse, tags=["consumo"],
             summary="Consumo do período de cobrança atual")
    async def usage(request: Request,
                    client: Optional[str] = Query(None, max_length=64,
                                                  description="Só administrador: mostra as chaves deste projeto"),
                    who: Principal = Depends(require("client", "admin"))):
        if who.role != "admin":
            if client is not None and client != who.name:
                raise HTTPException(403, "só o administrador escolhe o projeto")
            allowed, name = who.maptiler_keys, who.name
        elif client is None:
            allowed, name = None, None
        else:
            found = next((c for c in await request.app.state.storage.list_clients() if c["name"] == client), None)
            if found is None:
                raise HTTPException(404, "projeto não encontrado")
            allowed, name = frozenset(found.get("maptiler_keys") or ()), found["name"]
        return UsageResponse(**await request.app.state.monitor.usage(allowed, name))

    @app.get("/v1/clients", response_model=ClientsResponse, tags=["projetos"],
             summary="Projetos clientes", description="Sem as chaves dos projetos. Só administrador.")
    async def clients(request: Request, _who: Principal = Depends(require("admin"))):
        return ClientsResponse(clients=[
            ClientSummary(name=c["name"], active=c.get("active", True),
                          maptiler_keys=[mask_key(k) for k in c.get("maptiler_keys") or ()])
            for c in await request.app.state.storage.list_clients()])

    @app.get("/v1/usage/history", response_model=HistoryResponse, tags=["consumo"],
             summary="Consumo dia a dia")
    async def history(request: Request, days: int = Query(30, ge=1, le=400, description="Quantos dias"),
                      _who: Principal = Depends(require("client", "admin"))):
        return HistoryResponse(days=days, daily=await request.app.state.monitor.history(days))

    @app.get("/v1/checks", response_model=ChecksResponse, tags=["verificação"],
             summary="Verificações mais recentes", description="Bem-sucedidas e com falha. Só administrador.")
    async def checks_history(request: Request, limit: int = Query(20, ge=1, le=200),
                             _who: Principal = Depends(require("admin"))):
        records = await request.app.state.storage.recent_checks(limit)
        return ChecksResponse(checks=[check_summary(r) for r in records])

    @app.post("/v1/reports/daily", response_model=ReportResponse, tags=["alertas"],
              summary="Manda o relatório diário por e-mail",
              description="Usado pelo agendador às 9h (horário de Brasília). No máximo um relatório por dia: "
                          "chamadas repetidas devolvem already_sent.")
    async def daily_report(request: Request, _who: Principal = Depends(require("scheduler", "admin"))):
        usage_now = await request.app.state.monitor.usage(None)
        recent = await request.app.state.storage.recent_checks(100)
        return ReportResponse(**await request.app.state.alerts.send_daily_report(usage_now, recent))

    @app.get("/v1/alerts", response_model=AlertsResponse, tags=["alertas"], summary="Histórico de avisos",
             description="Cada faixa atingida, por métrica e canal, com o resultado do envio. Só administrador.")
    async def alerts_history(request: Request, limit: int = Query(50, ge=1, le=500),
                             _who: Principal = Depends(require("admin"))):
        docs = await request.app.state.storage.list_notifications(limit)
        return AlertsResponse(alerts=[alert_record(d) for d in docs])

    @app.post("/v1/alerts/test", response_model=TestAlertResponse, tags=["alertas"],
              summary="Manda um aviso de teste em cada canal configurado",
              description="Para conferir e-mail e WhatsApp depois de configurar. No máximo um por minuto.",
              responses={429: {"description": "Teste recente demais"}})
    async def alerts_test(request: Request, _who: Principal = Depends(require("admin"))):
        now, last = clock(), request.app.state.last_test_alert
        if last is not None and (now - last).total_seconds() < TEST_ALERT_INTERVAL_S:
            wait = int(TEST_ALERT_INTERVAL_S - (now - last).total_seconds()) + 1
            raise HTTPException(429, "um teste foi enviado há pouco; espere um minuto", headers={"Retry-After": str(wait)})
        request.app.state.last_test_alert = now
        return TestAlertResponse(results=await request.app.state.alerts.send_test())

    return app
