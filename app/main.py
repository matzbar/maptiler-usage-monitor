"""API do monitor de consumo do MapTiler.

Rodar: uvicorn app.main:create_app --factory
"""
import datetime as dt
import logging
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.errors import PyMongoError

from app import __version__
from app.auth import Principal, identify
from app.config import Settings, load_settings
from app.logs import setup_logging
from app.maptiler import AnalyticsClient
from app.monitoring.check import Monitor, utcnow
from app.ratelimit import RateLimiter, client_ip
from app.schemas import CheckResponse, HealthResponse, HistoryResponse, TooSoonResponse, UsageResponse
from app.security import headers_for
from app.storage import MemoryStorage, MongoStorage, Storage

logger = logging.getLogger("monitor.api")

DESCRIPTION = """
Acompanha o consumo de uma conta [MapTiler](https://www.maptiler.com/cloud/) pela
[Analytics API](https://docs.maptiler.com/cloud/admin-api/analytics/): requests e sessions do período de
cobrança, porcentagem da cota, faixa de consumo, projeção e histórico.

**Autenticação:** `Authorization: Bearer <token>`. Projetos clientes usam a própria chave (criada com
`python -m scripts.projetos criar`); o agendador usa o `CHECK_TOKEN`; o administrador, o `ADMIN_TOKEN`.

A credencial de serviço do MapTiler fica só neste servidor e nunca aparece nas respostas.
"""

bearer = HTTPBearer(auto_error=False, description="Chave do projeto, CHECK_TOKEN ou ADMIN_TOKEN")


def check_summary(record: dict) -> CheckResponse:
    fields = ("status", "checked_at", "duration_ms", "trigger", "period", "requests", "sessions", "level",
              "period_changed", "error")
    return CheckResponse(**{f: record.get(f) for f in fields})


def create_app(
    settings: Optional[Settings] = None,
    storage: Optional[Storage] = None,
    analytics=None,
    clock: Callable[[], dt.datetime] = utcnow,
    configure_logging: bool = True,
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
        monitor = Monitor(settings, st, an, clock)
        app.state.storage, app.state.monitor = st, monitor
        logger.info("monitor iniciado", extra={"event": "startup", "version": __version__,
                                               "storage": type(st).__name__, "commit": settings.build_commit})
        try:
            yield
        finally:
            await monitor.aclose()
            if analytics is None:
                await an.aclose()
            if storage is None:
                await st.close()

    app = FastAPI(title="MapTiler Usage Monitor", version=__version__, description=DESCRIPTION,
                  lifespan=lifespan, redoc_url=None)
    limiter = RateLimiter(settings.rate_limit_per_min)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        response = None
        if path != "/health":  # o health check do Render e do Docker não conta no limite
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
        return {"name": "maptiler-usage-monitor", "version": __version__, "docs": "/docs"}

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
    async def usage(request: Request, who: Principal = Depends(require("client", "admin"))):
        allowed = None if who.role == "admin" else who.maptiler_keys
        return UsageResponse(**await request.app.state.monitor.usage(allowed))

    @app.get("/v1/usage/history", response_model=HistoryResponse, tags=["consumo"],
             summary="Consumo dia a dia")
    async def history(request: Request, days: int = Query(30, ge=1, le=400, description="Quantos dias"),
                      _who: Principal = Depends(require("client", "admin"))):
        return HistoryResponse(days=days, daily=await request.app.state.monitor.history(days))

    return app
