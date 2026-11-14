"""Configuração lida das variáveis de ambiente. A lista completa, com explicações, está no .env.example."""
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

# Tamanho mínimo de CHECK_TOKEN e ADMIN_TOKEN: tokens curtos seriam fáceis de adivinhar.
MIN_TOKEN_LENGTH = 32


@dataclass(frozen=True)
class Settings:
    # Credencial de serviço do MapTiler. Dá acesso total à conta: repr=False para nunca aparecer em logs ou erros.
    maptiler_service_token: str = field(default="", repr=False)
    quota_requests: int = 100_000  # cota mensal de requests (plano Free do MapTiler em nov/2026)
    quota_sessions: int = 5_000  # cota mensal de sessions
    mongo_url: str = field(default="", repr=False)  # a URL leva usuário e senha do banco
    db_name: str = "maptiler_monitor"
    check_token: str = field(default="", repr=False)  # do agendador: só dispara verificações
    admin_token: str = field(default="", repr=False)  # do administrador: acesso a tudo
    http_timeout_s: float = 10.0  # tempo-limite de cada consulta ao MapTiler
    http_attempts: int = 3  # tentativas por consulta (a 2ª e a 3ª só em falha temporária)
    retry_base_s: float = 1.0  # espera antes da 2ª tentativa; dobra a cada nova tentativa
    min_check_interval_s: int = 10 * 60  # intervalo mínimo entre duas verificações
    refresh_after_s: int = 6 * 3600  # a partir daqui, uma leitura do consumo dispara uma verificação nova
    stale_after_s: int = 12 * 3600  # a partir daqui, o consumo guardado é marcado como desatualizado
    retention_days: int = 400  # quanto tempo o histórico fica guardado (13 meses e pouco)
    rate_limit_per_min: int = 60  # requisições por minuto por IP
    client_ip_header: str = ""
    trusted_proxy_hops: int = 0
    build_commit: Optional[str] = None


def _int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} deve ser um número inteiro (recebido: {raw!r})") from None
    if value < minimum:
        raise ValueError(f"{name} deve ser no mínimo {minimum}")
    return value


def _token(env: Mapping[str, str], name: str) -> str:
    value = (env.get(name) or "").strip()
    if value and len(value) < MIN_TOKEN_LENGTH:
        raise ValueError(f"{name} precisa ter pelo menos {MIN_TOKEN_LENGTH} caracteres")
    return value


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    """Lê e valida a configuração. Valores inválidos impedem a API de subir, com uma mensagem clara."""
    check, admin = _token(env, "CHECK_TOKEN"), _token(env, "ADMIN_TOKEN")
    if check and check == admin:
        raise ValueError("CHECK_TOKEN e ADMIN_TOKEN precisam ser diferentes")
    db_name = (env.get("DB_NAME") or "maptiler_monitor").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,38}", db_name):
        raise ValueError("DB_NAME aceita só letras, números, _ e - (até 38 caracteres)")
    # No Render (que define RENDER=true), o IP do visitante vem no CF-Connecting-IP, escrito pela Cloudflare.
    on_render = bool(env.get("RENDER"))
    return Settings(
        maptiler_service_token=(env.get("MAPTILER_SERVICE_TOKEN") or "").strip(),
        quota_requests=_int(env, "QUOTA_REQUESTS", 100_000, 1),
        quota_sessions=_int(env, "QUOTA_SESSIONS", 5_000, 1),
        mongo_url=(env.get("MONGO_URL") or "").strip(),
        db_name=db_name,
        check_token=check,
        admin_token=admin,
        http_timeout_s=float(_int(env, "HTTP_TIMEOUT_S", 10, 1)),
        http_attempts=_int(env, "HTTP_ATTEMPTS", 3, 1),
        min_check_interval_s=_int(env, "CHECK_MIN_INTERVAL_MIN", 10, 0) * 60,
        refresh_after_s=_int(env, "REFRESH_AFTER_HOURS", 6, 1) * 3600,
        stale_after_s=_int(env, "STALE_AFTER_HOURS", 12, 1) * 3600,
        retention_days=_int(env, "RETENTION_DAYS", 400, 1),
        rate_limit_per_min=_int(env, "RATE_LIMIT_PER_MIN", 60, 1),
        client_ip_header=(env.get("CLIENT_IP_HEADER") or ("cf-connecting-ip" if on_render else "")).strip().lower(),
        trusted_proxy_hops=_int(env, "TRUSTED_PROXY_HOPS", 2 if on_render else 0, 0),
        build_commit=(env.get("RENDER_GIT_COMMIT") or env.get("GIT_COMMIT") or "")[:7] or None,
    )
