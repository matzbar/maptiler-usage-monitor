"""Regras de alerta: quais faixas o consumo atingiu e o que fazer em cada canal. Funções puras.

Requests e sessions são avaliados separadamente. Em cada canal, só a faixa mais alta atingida gera envio; as
mais baixas do mesmo canal ficam como "superadas" (um salto de 60% para 92% manda só o aviso de 90%).
"""
from dataclasses import dataclass

from app.config import AlertRules


@dataclass(frozen=True)
class Planned:
    threshold: int
    channel: str
    action: str  # record (só registra, para o painel), notify (enviar) ou skip (superada por faixa mais alta)


def reached(used: int, quota: int, threshold: int) -> bool:
    return used * 100 >= threshold * quota  # conta inteira: sem erro de arredondamento na fronteira


def plan(used: int, quota: int, rules: AlertRules) -> list[Planned]:
    hit = [(threshold, channels) for threshold, channels in rules if reached(used, quota, threshold)]
    top: dict[str, int] = {}
    for threshold, channels in hit:
        for channel in channels:
            top[channel] = max(top.get(channel, 0), threshold)
    planned = []
    for threshold, channels in hit:
        for channel in sorted(channels):
            if channel == "panel":
                action = "record"
            else:
                action = "notify" if threshold == top[channel] else "skip"
            planned.append(Planned(threshold, channel, action))
    return planned
