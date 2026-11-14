"""Texto dos avisos, em português, com números no formato brasileiro (2.186 e 81,2%)."""
import datetime as dt
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo


class Message(NamedTuple):
    subject: str  # assunto do e-mail
    text: str  # corpo do e-mail
    short: str  # mensagem de WhatsApp


def br_int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def br_pct(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "%"


def br_date(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


def br_datetime(moment: dt.datetime, timezone: str) -> str:
    local = moment.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y às %H:%M")
    return f"{local} (horário de Brasília)" if timezone == "America/Sao_Paulo" else f"{local} ({timezone})"


def usage_message(doc: dict, timezone: str) -> Message:
    metric, threshold = doc["metric"], doc["threshold"]
    pct, used, quota = br_pct(doc["percent"]), br_int(doc["used"]), br_int(doc["quota"])
    since, until = br_date(doc["period"]["since"]), br_date(doc["period"]["until"])
    projection: Optional[float] = doc.get("projection_percent")
    if threshold >= 100:
        subject = f"[MapTiler] Cota de {metric} esgotada ({pct})"
    else:
        subject = f"[MapTiler] {threshold}% da cota de {metric} usada"
    lines = [
        f"O consumo de {metric} da conta MapTiler chegou a {pct} da cota ({used} de {quota}).",
        f"Período de cobrança: {since} a {until}.",
    ]
    if projection is not None:
        lines.append(f"Projeção até o fim do período: {br_pct(projection)} da cota.")
    if threshold >= 100:
        lines.append("No plano Free, o MapTiler pausa o serviço até o início do próximo período.")
    lines += [
        f"Verificado em {br_datetime(doc['check_at'], timezone)}.",
        "",
        f"Este aviso (faixa de {threshold}%) é enviado uma única vez por período.",
    ]
    short = f"*MapTiler*: {metric} em {pct} da cota ({used} de {quota}). Período até {until}."
    if projection is not None:
        short += f" Projeção: {br_pct(projection)}."
    return Message(subject, "\n".join(lines), short)


def monitor_down_message(doc: dict, timezone: str) -> Message:
    failures, error = doc["failures"], doc["error"]
    last_ok = doc.get("last_ok_at")
    since_ok = f"o último consumo válido é de {br_datetime(last_ok, timezone)}" if last_ok else \
        "ainda não houve nenhuma verificação bem-sucedida"
    text = "\n".join([
        f"As últimas {failures} verificações do consumo do MapTiler falharam; {since_ok}.",
        f"Último erro: {error['message']} ({error['kind']}).",
        "Enquanto isso, a API continua mostrando o último consumo válido, marcado como desatualizado depois de "
        "algumas horas.",
        "",
        "Este aviso é enviado uma vez por sequência de falhas.",
    ])
    short = f"*MapTiler Monitor*: {failures} verificações seguidas falharam ({error['kind']})."
    return Message(f"[MapTiler] Monitor sem dados há {failures} verificações", text, short)


def ping_message(doc: dict, timezone: str) -> Message:
    when = br_datetime(doc["created_at"], timezone)
    text = f"Teste do MapTiler Usage Monitor, enviado em {when}. Se você recebeu, este canal de aviso funciona."
    return Message("[MapTiler] Teste de aviso", text, f"*MapTiler Monitor*: teste de aviso ({when}).")


def build_message(doc: dict, timezone: str) -> Message:
    builders = {"usage": usage_message, "monitor_down": monitor_down_message, "test": ping_message}
    return builders[doc["kind"]](doc, timezone)
