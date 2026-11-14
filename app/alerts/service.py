"""Serviço de alertas: roda depois de cada verificação.

1. Verificação bem-sucedida: para requests e sessions, separadamente, cria um aviso por faixa atingida e canal.
   O _id do aviso (período:métrica:faixa:canal) é único no banco, então cada aviso nasce uma vez por período,
   mesmo com verificações simultâneas. Um período novo tem outro _id: as faixas voltam a valer.
2. Verificação com falha: depois de ALERT_FAILURE_STREAK falhas seguidas, um e-mail "monitor sem dados", uma vez
   por sequência de falhas.
3. Avisos que falharam são tentados de novo nas verificações seguintes, até ALERT_MAX_ATTEMPTS vezes; se uma faixa
   mais alta do mesmo canal já foi atingida, o aviso antigo fica como superado.

Status de um aviso: recorded (só registro, para o painel), sending, sent, failed ou skipped.
"""
import datetime as dt
import logging
from typing import Callable

from app.alerts.channels import ChannelError
from app.alerts.messages import build_message
from app.alerts.rules import plan
from app.config import Settings
from app.storage import Storage

logger = logging.getLogger("monitor.alerts")

METRICS = ("requests", "sessions")
LEASE = dt.timedelta(minutes=5)  # tempo para um envio terminar antes de ser considerado interrompido
RETRY_GAP = dt.timedelta(minutes=10)  # espera mínima entre duas tentativas do mesmo aviso
RETRY_WINDOW = dt.timedelta(days=3)  # avisos mais antigos que isso não são mais reenviados
SUPERSEDED = "superado por uma faixa mais alta"
NOT_CONFIGURED = "canal não configurado"


class AlertService:
    def __init__(self, settings: Settings, storage: Storage, channels: dict, clock: Callable[[], dt.datetime]):
        self.settings = settings
        self.storage = storage
        self.channels = channels
        self.clock = clock

    async def after_check(self, record: dict) -> list[dict]:
        """Processa os alertas de uma verificação e devolve o resumo dos avisos criados ou reenviados."""
        if record["status"] == "ok":
            touched = await self._evaluate(record)
        else:
            touched = await self._monitor_down(record)
        touched += await self._retry(exclude={doc["_id"] for doc in touched})
        return [self.summary(doc) for doc in touched]

    @staticmethod
    def summary(doc: dict) -> dict:
        return {key: doc.get(key) for key in ("kind", "metric", "threshold", "channel", "status")}

    # ---------- criação ----------

    def _new(self, now: dt.datetime, channel: str, action: str, **fields) -> dict:
        doc = {"channel": channel, "created_at": now, "updated_at": now, "attempts": 0, "sent_at": None,
               "lease_until": None, "last_error": None, **fields}
        if action == "record":
            doc["status"] = "recorded"
        elif action == "skip":
            doc.update(status="skipped", last_error=SUPERSEDED)
        elif not self._configured(channel):
            doc.update(status="skipped", last_error=NOT_CONFIGURED)
        else:
            doc.update(status="sending", attempts=1, lease_until=now + LEASE)
        return doc

    def _configured(self, channel: str) -> bool:
        target = self.channels.get(channel)
        return target is not None and target.configured

    async def _evaluate(self, record: dict) -> list[dict]:
        now, period = self.clock(), record["period"]
        docs = []
        for metric in METRICS:
            usage = record[metric]
            for item in plan(usage["used"], usage["quota"], self.settings.alert_rules):
                docs.append(self._new(
                    now, item.channel, item.action, kind="usage",
                    _id=f"{period['since']}:{metric}:{item.threshold}:{item.channel}",
                    period=period, metric=metric, threshold=item.threshold, percent=usage["percent"],
                    used=usage["used"], quota=usage["quota"],
                    projection_percent=record["projection"][f"{metric}_percent"], check_at=record["checked_at"]))
        return await self._claim_and_send(docs)

    async def _monitor_down(self, record: dict) -> list[dict]:
        streak = self.settings.alert_failure_streak
        recent = await self.storage.recent_checks(streak)
        if len(recent) < streak or any(check["status"] != "failed" for check in recent):
            return []
        last_ok = await self.storage.last_ok_check()
        # Uma sequência de falhas é identificada pela última verificação boa antes dela.
        streak_id = last_ok["checked_at"].isoformat() if last_ok else "nunca"
        doc = self._new(self.clock(), "email", "notify", kind="monitor_down", _id=f"monitor_down:{streak_id}:email",
                        period=last_ok["period"] if last_ok else None, metric=None, threshold=None,
                        failures=streak, error=record["error"],
                        last_ok_at=last_ok["checked_at"] if last_ok else None)
        return await self._claim_and_send([doc])

    async def _claim_and_send(self, docs: list[dict]) -> list[dict]:
        touched = []
        for doc in docs:
            if not await self.storage.claim_notification(doc):
                continue  # já existe: outra verificação (ou esta mesma, antes) cuidou dele
            touched.append(doc)
            logger.info("aviso registrado", extra={"event": "notification_created", **self._log_fields(doc)})
            if doc["status"] == "sending":
                await self._deliver(doc)
        return touched

    # ---------- envio ----------

    async def _deliver(self, doc: dict) -> None:
        try:
            await self.channels[doc["channel"]].send(build_message(doc, self.settings.alert_timezone), doc["_id"])
        except ChannelError as exc:
            fields = {"status": "failed", "last_error": str(exc)}
        except Exception:
            logger.exception("erro inesperado ao enviar aviso", extra={"event": "notification_crashed"})
            fields = {"status": "failed", "last_error": "erro interno ao enviar"}
        else:
            fields = {"status": "sent", "last_error": None}
        now = self.clock()
        fields.update(updated_at=now, lease_until=None, sent_at=now if fields["status"] == "sent" else None)
        doc.update(fields)
        await self.storage.update_notification(doc["_id"], fields)
        if doc["status"] == "sent":
            logger.info("aviso enviado", extra={"event": "notification_sent", **self._log_fields(doc)})
        else:
            logger.warning("aviso não enviado: " + doc["last_error"],
                           extra={"event": "notification_failed", **self._log_fields(doc)})

    @staticmethod
    def _log_fields(doc: dict) -> dict:
        return {"notification": doc["_id"], "channel": doc["channel"], "status": doc["status"],
                "attempts": doc["attempts"]}

    # ---------- novas tentativas ----------

    async def _retry(self, exclude: set) -> list[dict]:
        now = self.clock()
        touched = []
        for doc in await self.storage.retry_candidates(now - RETRY_WINDOW, self.settings.alert_max_attempts):
            if doc["_id"] in exclude:
                continue
            reason = None
            if doc["kind"] == "usage" and await self._superseded(doc):
                reason = SUPERSEDED
            elif not self._configured(doc["channel"]):
                reason = NOT_CONFIGURED
            if reason:
                fields = {"status": "skipped", "last_error": reason, "updated_at": now, "lease_until": None}
                await self.storage.update_notification(doc["_id"], fields)
                doc.update(fields)
                touched.append(doc)
                continue
            claimed = await self.storage.claim_retry(doc["_id"], now, now + LEASE, now - RETRY_GAP,
                                                     self.settings.alert_max_attempts)
            if claimed is None:
                continue  # cedo demais, ou outra verificação já reservou
            await self._deliver(claimed)
            touched.append(claimed)
        return touched

    async def _superseded(self, doc: dict) -> bool:
        same_period = await self.storage.notifications_for_period(doc["period"]["since"])
        return any(other["metric"] == doc["metric"] and other["channel"] == doc["channel"]
                   and other["threshold"] > doc["threshold"] for other in same_period)

    # ---------- teste ----------

    async def send_test(self) -> list[dict]:
        """Manda uma mensagem de teste em cada canal configurado (e registra no histórico)."""
        now, results = self.clock(), []
        for channel in ("email", "whatsapp"):
            if not self._configured(channel):
                results.append({"channel": channel, "status": "not_configured", "error": None})
                continue
            doc = self._new(now, channel, "notify", kind="test", _id=f"test:{now.isoformat()}:{channel}",
                            period=None, metric=None, threshold=None)
            if await self.storage.claim_notification(doc):
                await self._deliver(doc)
            results.append({"channel": channel, "status": doc["status"], "error": doc["last_error"]})
        return results

    async def aclose(self) -> None:
        for channel in self.channels.values():
            await channel.aclose()
