"""Motor de cálculo: transforma as respostas da Analytics API em consumo, percentuais, faixa e projeção.

Só funções puras (sem rede nem banco).
"""
import datetime as dt
from collections import defaultdict
from typing import Mapping, Optional

from app.maptiler import AnalyticsError, Timeline

# Faixas do plano, da mais alta para a mais baixa. O nível é o da faixa mais alta atingida.
LEVELS = ((100, "critical"), (90, "very_high"), (80, "high"), (70, "warning"), (50, "notice"))
LEVEL_ORDER = ("normal", "notice", "warning", "high", "very_high", "critical")
METRIC = {"request": "requests", "session": "sessions"}


def percent(used: int, quota: int) -> float:
    """Porcentagem da cota, truncada em 1 casa decimal: nunca mostra mais do que o consumo real."""
    return (used * 1000 // quota) / 10


def level_for(used: int, quota: int) -> str:
    for threshold, name in LEVELS:
        if used * 100 >= threshold * quota:  # conta inteira: sem erro de arredondamento na fronteira
            return name
    return "normal"


def highest(*levels: str) -> str:
    return max(levels, key=LEVEL_ORDER.index)


def mask_key(label: Optional[str]) -> str:
    """Chave do MapTiler mascarada para exibição: 7lbZ…aC."""
    if not label or len(label) <= 8:
        return "…"
    return f"{label[:4]}…{label[-2:]}"


def build_snapshot(
    timelines: Mapping[tuple[str, str], Timeline], quota_requests: int, quota_sessions: int, today: dt.date
) -> dict:
    """Junta as consultas (serviços e chaves × requests e sessions) num retrato do período atual.

    O total da conta vem da consulta por serviço; a consulta por chave só detalha quem consumiu.
    """
    periods = {(t.since, t.until) for t in timelines.values()}
    if len(periods) != 1:
        raise AnalyticsError("invalid_response", "as consultas ao MapTiler vieram com períodos diferentes")
    since, until = periods.pop()

    totals = {"requests": 0, "sessions": 0}
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"requests": 0, "sessions": 0})
    services: dict[str, dict] = {}
    keys: dict[str, dict] = {}
    for (classifier, group), timeline in timelines.items():
        metric = METRIC[group]
        legend = {item.item_id: item for item in timeline.legend}
        for dataset in timeline.datasets:
            item_id = dataset.item_id or "?"
            info = legend.get(item_id)
            used = sum(point.value for point in dataset.data)
            if classifier == "services":
                totals[metric] += used
                for point in dataset.data:
                    daily[point.date.isoformat()][metric] += point.value
                row = services.setdefault(item_id, {"service": item_id, "label": info.label if info else None,
                                                    "requests": 0, "sessions": 0})
            else:
                row = keys.setdefault(item_id, {"key_id": item_id, "key_label": info.label if info else None,
                                                "name": info.description if info else None,
                                                "requests": 0, "sessions": 0})
            row[metric] += used

    # Projeção até o fim do período pela média diária até hoje.
    days_in_period = (until - since).days + 1
    days_elapsed = min(max((today - since).days + 1, 1), days_in_period)

    def project(used: int) -> int:
        return round(used / days_elapsed * days_in_period)

    projected_requests, projected_sessions = project(totals["requests"]), project(totals["sessions"])
    by_usage = lambda row: (-row["requests"], -row["sessions"])  # noqa: E731
    return {
        "period": {"since": since.isoformat(), "until": until.isoformat()},
        "requests": {"used": totals["requests"], "quota": quota_requests,
                     "percent": percent(totals["requests"], quota_requests)},
        "sessions": {"used": totals["sessions"], "quota": quota_sessions,
                     "percent": percent(totals["sessions"], quota_sessions)},
        "level": highest(level_for(totals["requests"], quota_requests),
                         level_for(totals["sessions"], quota_sessions)),
        "projection": {
            "requests": projected_requests,
            "requests_percent": percent(projected_requests, quota_requests),
            "sessions": projected_sessions,
            "sessions_percent": percent(projected_sessions, quota_sessions),
            "based_on_days": days_elapsed,
            "days_in_period": days_in_period,
        },
        "daily": [{"date": day, **daily[day]} for day in sorted(daily)],
        "by_service": sorted(services.values(), key=lambda r: (*by_usage(r), r["service"])),
        "by_key": sorted(keys.values(), key=lambda r: (*by_usage(r), r["key_id"])),
    }
