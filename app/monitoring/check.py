"""Verificação do consumo (busca → calcula → grava) e leitura do consumo guardado.

Regras:
  - uma verificação por vez, e no máximo uma a cada CHECK_MIN_INTERVAL_MIN;
  - falha na consulta é gravada como falha (status failed) e o último consumo válido continua valendo;
  - se ninguém verificar por REFRESH_AFTER_HOURS, a próxima leitura dispara uma verificação em segundo plano.
"""
import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app.config import Settings
from app.maptiler import AnalyticsError
from app.monitoring.engine import build_snapshot, mask_key
from app.storage import Storage

logger = logging.getLogger("monitor.check")

# Consultas de cada verificação: o MapTiler aceita um grupo por vez.
QUERIES = (("services", "request"), ("services", "session"), ("api_keys", "request"), ("api_keys", "session"))


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class CheckOutcome:
    outcome: str  # done, too_soon ou busy
    record: Optional[dict] = None  # done: a verificação feita; too_soon: a última
    retry_after_s: int = 0


class Monitor:
    def __init__(self, settings: Settings, storage: Storage, analytics, clock: Callable[[], dt.datetime] = utcnow,
                 alerts=None):
        self.settings = settings
        self.storage = storage
        self.analytics = analytics
        self.clock = clock
        self.alerts = alerts  # AlertService (opcional): roda depois de cada verificação
        self._lock = asyncio.Lock()
        self._background: Optional[asyncio.Task] = None

    async def check_now(self, trigger: str) -> CheckOutcome:
        """Roda uma verificação, respeitando o intervalo mínimo. trigger: schedule, manual ou on_demand."""
        if self._lock.locked():
            return CheckOutcome("busy")
        async with self._lock:
            last = await self.storage.last_check()
            if last is not None:
                elapsed = (self.clock() - last["checked_at"]).total_seconds()
                if elapsed < self.settings.min_check_interval_s:
                    return CheckOutcome("too_soon", last, int(self.settings.min_check_interval_s - elapsed) + 1)
            return CheckOutcome("done", await self._run(trigger))

    async def _run(self, trigger: str) -> dict:
        started_at, t0 = self.clock(), time.monotonic()
        logger.info("verificação iniciada", extra={"event": "check_started", "trigger": trigger})
        record: dict = {"checked_at": started_at, "trigger": trigger}
        try:
            results = await asyncio.gather(*(self.analytics.timeline(c, g) for c, g in QUERIES),
                                           return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            snapshot = build_snapshot(dict(zip(QUERIES, results)), self.settings.quota_requests,
                                      self.settings.quota_sessions, started_at.date())
        except AnalyticsError as exc:
            record.update(status="failed", error={"kind": exc.kind, "message": exc.message,
                                                  "http_status": exc.http_status})
        except Exception:
            logger.exception("erro inesperado na verificação", extra={"event": "check_crashed"})
            record.update(status="failed", error={"kind": "internal", "message": "erro interno do monitor",
                                                  "http_status": None})
        else:
            previous = await self.storage.last_ok_check()
            changed = previous is not None and previous["period"]["since"] != snapshot["period"]["since"]
            if changed:
                logger.info("novo período de cobrança", extra={
                    "event": "billing_period_changed", "previous_since": previous["period"]["since"],
                    "since": snapshot["period"]["since"]})
            record.update(status="ok", period_changed=changed, **snapshot)
        record["duration_ms"] = round((time.monotonic() - t0) * 1000)
        await self.storage.save_check(record)
        if self.alerts is not None:
            # Um problema nos alertas não desfaz a verificação, que já está gravada.
            try:
                record["notifications"] = await self.alerts.after_check(record)
            except Exception:
                logger.exception("falha ao processar os alertas", extra={"event": "alerts_crashed"})
                record["notifications"] = []
        extra = {"event": "check_finished", "trigger": trigger, "status": record["status"],
                 "duration_ms": record["duration_ms"]}
        if record["status"] == "ok":
            logger.info("verificação concluída", extra={**extra, "usage_level": record["level"],
                                                        "requests_percent": record["requests"]["percent"],
                                                        "sessions_percent": record["sessions"]["percent"]})
        else:
            logger.warning("verificação falhou: " + record["error"]["message"],
                           extra={**extra, "kind": record["error"]["kind"]})
        return record

    # ---------- leitura ----------

    async def usage(self, allowed_keys: Optional[frozenset[str]]) -> dict:
        """Consumo guardado mais recente. allowed_keys: chaves do MapTiler que o projeto pode ver (None = todas)."""
        now = self.clock()
        last_ok = await self.storage.last_ok_check()
        last = await self.storage.last_check()
        period_over = last_ok is not None and now.date() > dt.date.fromisoformat(last_ok["period"]["until"])
        self._maybe_refresh(now, last, period_over)

        last_error = None
        if last is not None and last["status"] == "failed" and (
                last_ok is None or last["checked_at"] > last_ok["checked_at"]):
            last_error = {"checked_at": last["checked_at"], "kind": last["error"]["kind"],
                          "message": last["error"]["message"]}
        if last_ok is None:
            # Nunca houve verificação bem-sucedida: sem números, para ninguém confundir com consumo zero.
            return {"status": "unavailable", "checked_at": None, "last_error": last_error}

        age_s = (now - last_ok["checked_at"]).total_seconds()
        keys = [
            {"key": mask_key(k["key_label"]), "name": k["name"], "requests": k["requests"], "sessions": k["sessions"]}
            for k in last_ok["by_key"]
            if allowed_keys is None or k["key_id"] in allowed_keys or k["key_label"] in allowed_keys
        ]
        return {
            "status": "stale" if age_s > self.settings.stale_after_s or period_over else "ok",
            "checked_at": last_ok["checked_at"],
            "period": last_ok["period"],
            "requests": last_ok["requests"],
            "sessions": last_ok["sessions"],
            "level": last_ok["level"],
            "projection": last_ok["projection"],
            "keys": keys,
            "services": last_ok["by_service"],
            "last_error": last_error,
        }

    async def history(self, days: int) -> list[dict]:
        """Consumo dia a dia dos últimos `days` dias, juntando os períodos de cobrança."""
        now = self.clock()
        first_day = (now.date() - dt.timedelta(days=days - 1)).isoformat()
        # Um período dura cerca de 31 dias: buscar desde days + 62 dias cobre o período que começou antes.
        checks = await self.storage.latest_ok_per_period(now - dt.timedelta(days=days + 62))
        merged: dict[str, dict] = {}
        for check in sorted(checks, key=lambda c: c["checked_at"], reverse=True):
            for day in check["daily"]:
                if day["date"] >= first_day:
                    merged.setdefault(day["date"], day)
        return [merged[d] for d in sorted(merged)]

    # ---------- atualização sob demanda ----------

    def _maybe_refresh(self, now: dt.datetime, last: Optional[dict], period_over: bool) -> None:
        if self._lock.locked() or (self._background is not None and not self._background.done()):
            return
        age_s = None if last is None else (now - last["checked_at"]).total_seconds()
        due = (age_s is None or age_s > self.settings.refresh_after_s
               or (period_over and age_s > self.settings.min_check_interval_s))
        if due:
            self._background = asyncio.create_task(self._background_check())

    async def _background_check(self) -> None:
        try:
            await self.check_now("on_demand")
        except Exception:
            logger.exception("falha na verificação sob demanda", extra={"event": "on_demand_crashed"})

    async def wait_background(self) -> None:
        if self._background is not None:
            await asyncio.shield(self._background)

    async def aclose(self) -> None:
        if self._background is not None and not self._background.done():
            self._background.cancel()
            try:
                await self._background
            except asyncio.CancelledError:
                pass
