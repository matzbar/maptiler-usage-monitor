"""Configuração lida das variáveis de ambiente. A lista completa, com explicações, está no .env.example."""
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Tamanho mínimo de CHECK_TOKEN e ADMIN_TOKEN: tokens curtos seriam fáceis de adivinhar.
MIN_TOKEN_LENGTH = 32

# Faixas de alerta: "porcentagem:canais", canais separados por "+". panel = só registra (aparece no painel).
DEFAULT_ALERT_RULES = "50:panel,70:panel,80:email,90:email+whatsapp,100:email+whatsapp"
ALERT_CHANNELS = frozenset({"panel", "email", "whatsapp"})
AlertRules = tuple[tuple[int, frozenset[str]], ...]


def parse_alert_rules(raw: str) -> AlertRules:
    rules = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        threshold, _, channels = part.partition(":")
        try:
            value = int(threshold)
        except ValueError:
            raise ValueError(f"ALERT_RULES: faixa inválida em {part!r} (use porcentagem:canais)") from None
        if not 1 <= value <= 1000:
            raise ValueError(f"ALERT_RULES: a faixa {value} precisa ficar entre 1 e 1000")
        names = frozenset(c.strip() for c in channels.split("+") if c.strip())
        if not names or names - ALERT_CHANNELS:
            raise ValueError(f"ALERT_RULES: em {part!r}, use os canais panel, email ou whatsapp")
        rules.append((value, names))
    thresholds = [t for t, _ in rules]
    if len(set(thresholds)) != len(thresholds):
        raise ValueError("ALERT_RULES: cada faixa pode aparecer uma vez só")
    return tuple(sorted(rules, key=lambda r: r[0]))


def _emails(raw: str) -> tuple[str, ...]:
    emails = tuple(e.strip() for e in raw.split(",") if e.strip())
    for email in emails:
        if not re.fullmatch(r"[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+", email):
            raise ValueError("ALERT_EMAIL_TO tem um endereço de e-mail inválido")
    return emails


def _phone(raw: str) -> str:
    digits = re.sub(r"[\s().-]", "", raw)
    if not digits:
        return ""
    if not re.fullmatch(r"\+?\d{8,15}", digits):
        raise ValueError("ALERT_WHATSAPP_PHONE deve ter o código do país e o número, como +5511999998888")
    return digits if digits.startswith("+") else "+" + digits


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
    # Alertas
    alert_rules: AlertRules = field(default_factory=lambda: parse_alert_rules(DEFAULT_ALERT_RULES))
    resend_api_key: str = field(default="", repr=False)
    alert_email_from: str = "MapTiler Monitor <onboarding@resend.dev>"
    alert_email_to: tuple[str, ...] = field(default=(), repr=False)  # dado pessoal: fora dos logs
    callmebot_api_key: str = field(default="", repr=False)
    alert_whatsapp_phone: str = field(default="", repr=False)
    alert_max_attempts: int = 3  # tentativas de envio de cada aviso, nas verificações seguintes
    alert_failure_streak: int = 3  # verificações seguidas com falha antes do e-mail "monitor sem dados"
    alert_timezone: str = "America/Sao_Paulo"  # fuso dos horários escritos nas mensagens


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
    timezone = (env.get("ALERT_TIMEZONE") or "America/Sao_Paulo").strip()
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"ALERT_TIMEZONE: fuso desconhecido {timezone!r}") from None
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
        alert_rules=parse_alert_rules(env.get("ALERT_RULES") or DEFAULT_ALERT_RULES),
        resend_api_key=(env.get("RESEND_API_KEY") or "").strip(),
        alert_email_from=(env.get("ALERT_EMAIL_FROM") or "MapTiler Monitor <onboarding@resend.dev>").strip(),
        alert_email_to=_emails(env.get("ALERT_EMAIL_TO") or ""),
        callmebot_api_key=(env.get("CALLMEBOT_API_KEY") or "").strip(),
        alert_whatsapp_phone=_phone(env.get("ALERT_WHATSAPP_PHONE") or ""),
        alert_max_attempts=_int(env, "ALERT_MAX_ATTEMPTS", 3, 1),
        alert_failure_streak=_int(env, "ALERT_FAILURE_STREAK", 3, 1),
        alert_timezone=timezone,
    )
