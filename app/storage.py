"""Armazenamento: MongoDB em produção; memória nos testes e no uso local sem banco.

Coleções (database DB_NAME, padrão maptiler_monitor):
  - maptiler_usage_monitor: uma verificação por documento (status ok ou failed), guardada por RETENTION_DAYS;
  - maptiler_notification_history: um aviso por documento. O _id junta período, métrica, faixa e canal, então
    o mesmo aviso não pode ser criado duas vezes, nem por verificações simultâneas;
  - clients: projetos clientes, com o hash da chave de acesso (nunca a chave).
"""
import copy
import datetime as dt
import logging
from typing import Optional, Protocol

logger = logging.getLogger("monitor.storage")

CHECKS = "maptiler_usage_monitor"
NOTIFICATIONS = "maptiler_notification_history"
CLIENTS = "clients"


def _retry_ready(doc: dict, now: dt.datetime, failed_before: dt.datetime, max_attempts: int) -> bool:
    """Pode tentar de novo: falhou há algum tempo, ou ficou "enviando" além do prazo (o processo caiu no meio)."""
    if doc["attempts"] >= max_attempts:
        return False
    if doc["status"] == "failed":
        return doc["updated_at"] <= failed_before
    return doc["status"] == "sending" and doc.get("lease_until") is not None and doc["lease_until"] < now


class DuplicateClient(Exception):
    """Já existe um projeto com esse nome (ou, na prática impossível, com essa chave)."""


class Storage(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def save_check(self, record: dict) -> None: ...
    async def last_check(self) -> Optional[dict]: ...
    async def last_ok_check(self) -> Optional[dict]: ...
    async def latest_ok_per_period(self, since: dt.datetime) -> list[dict]: ...
    async def recent_checks(self, limit: int) -> list[dict]: ...
    async def claim_notification(self, doc: dict) -> bool: ...
    async def update_notification(self, notification_id: str, fields: dict) -> None: ...
    async def claim_retry(self, notification_id: str, now: dt.datetime, lease_until: dt.datetime,
                          failed_before: dt.datetime, max_attempts: int) -> Optional[dict]: ...
    async def retry_candidates(self, created_since: dt.datetime, max_attempts: int) -> list[dict]: ...
    async def notifications_for_period(self, period_since: str) -> list[dict]: ...
    async def list_notifications(self, limit: int) -> list[dict]: ...
    async def add_client(self, doc: dict) -> None: ...
    async def client_by_hash(self, key_hash: str) -> Optional[dict]: ...
    async def list_clients(self) -> list[dict]: ...
    async def set_client_active(self, name: str, active: bool) -> bool: ...


class MemoryStorage:
    """Tudo na memória do processo: some ao reiniciar. Para testes e uso local."""

    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.notifications: dict[str, dict] = {}
        self.clients: list[dict] = []

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save_check(self, record: dict) -> None:
        self.checks.append(copy.deepcopy(record))

    def _latest(self, status: Optional[str] = None) -> Optional[dict]:
        found = [c for c in self.checks if status is None or c["status"] == status]
        return copy.deepcopy(max(found, key=lambda c: c["checked_at"])) if found else None

    async def last_check(self) -> Optional[dict]:
        return self._latest()

    async def last_ok_check(self) -> Optional[dict]:
        return self._latest("ok")

    async def latest_ok_per_period(self, since: dt.datetime) -> list[dict]:
        latest: dict[str, dict] = {}
        for check in sorted(self.checks, key=lambda c: c["checked_at"], reverse=True):
            if check["status"] == "ok" and check["checked_at"] >= since:
                latest.setdefault(check["period"]["since"], check)
        return [copy.deepcopy(c) for c in latest.values()]

    async def recent_checks(self, limit: int) -> list[dict]:
        return copy.deepcopy(sorted(self.checks, key=lambda c: c["checked_at"], reverse=True)[:limit])

    async def claim_notification(self, doc: dict) -> bool:
        if doc["_id"] in self.notifications:
            return False
        self.notifications[doc["_id"]] = copy.deepcopy(doc)
        return True

    async def update_notification(self, notification_id: str, fields: dict) -> None:
        self.notifications[notification_id].update(copy.deepcopy(fields))

    async def claim_retry(self, notification_id, now, lease_until, failed_before, max_attempts):
        doc = self.notifications.get(notification_id)
        if doc is None or not _retry_ready(doc, now, failed_before, max_attempts):
            return None
        doc.update(status="sending", lease_until=lease_until, updated_at=now, attempts=doc["attempts"] + 1)
        return copy.deepcopy(doc)

    async def retry_candidates(self, created_since: dt.datetime, max_attempts: int) -> list[dict]:
        found = [d for d in self.notifications.values() if d["status"] in ("failed", "sending")
                 and d["attempts"] < max_attempts and d["created_at"] >= created_since]
        return copy.deepcopy(sorted(found, key=lambda d: d["created_at"]))

    async def notifications_for_period(self, period_since: str) -> list[dict]:
        return copy.deepcopy([d for d in self.notifications.values()
                              if (d.get("period") or {}).get("since") == period_since])

    async def list_notifications(self, limit: int) -> list[dict]:
        ordered = sorted(self.notifications.values(), key=lambda d: (d["created_at"], d["_id"]), reverse=True)
        return copy.deepcopy(ordered[:limit])

    async def add_client(self, doc: dict) -> None:
        if any(c["name"] == doc["name"] or c["key_hash"] == doc["key_hash"] for c in self.clients):
            raise DuplicateClient(doc["name"])
        self.clients.append(copy.deepcopy(doc))

    async def client_by_hash(self, key_hash: str) -> Optional[dict]:
        return next((copy.deepcopy(c) for c in self.clients if c["key_hash"] == key_hash), None)

    async def list_clients(self) -> list[dict]:
        return [{k: v for k, v in c.items() if k != "key_hash"} for c in sorted(self.clients, key=lambda c: c["name"])]

    async def set_client_active(self, name: str, active: bool) -> bool:
        for client in self.clients:
            if client["name"] == name:
                client["active"] = active
                return True
        return False


class MongoStorage:
    def __init__(self, url: str, db_name: str, retention_days: int):
        from pymongo import AsyncMongoClient

        # tz_aware: as datas voltam com fuso (UTC), comparáveis às do relógio da API.
        self._client = AsyncMongoClient(url, tz_aware=True, serverSelectionTimeoutMS=5000,
                                        appname="maptiler-usage-monitor")
        self._db = self._client[db_name]
        self._checks = self._db[CHECKS]
        self._notifications = self._db[NOTIFICATIONS]
        self._clients = self._db[CLIENTS]
        self._retention_s = retention_days * 86400

    async def connect(self) -> None:
        """Cria os índices (se já existirem, nada muda)."""
        await self._ensure_ttl(CHECKS, "checked_at", "checked_at_ttl")
        await self._checks.create_index([("status", 1), ("checked_at", -1)], name="status_checked_at")
        await self._ensure_ttl(NOTIFICATIONS, "created_at", "created_at_ttl")
        await self._notifications.create_index([("period.since", 1), ("metric", 1), ("channel", 1)],
                                               name="period_metric_channel")
        await self._notifications.create_index([("status", 1), ("created_at", 1)], name="status_created_at")
        await self._clients.create_index("key_hash", unique=True, name="key_hash_unique")
        await self._clients.create_index("name", unique=True, name="name_unique")

    async def _ensure_ttl(self, collection: str, field_name: str, index_name: str) -> None:
        """Índice TTL: o MongoDB apaga sozinho os documentos mais antigos que RETENTION_DAYS."""
        from pymongo.errors import OperationFailure

        try:
            await self._db[collection].create_index(field_name, name=index_name,
                                                    expireAfterSeconds=self._retention_s)
        except OperationFailure as exc:
            if exc.code not in (85, 86):  # IndexOptionsConflict / IndexKeySpecsConflict: só o prazo mudou
                raise
            await self._db.command("collMod", collection,
                                   index={"name": index_name, "expireAfterSeconds": self._retention_s})
            logger.info("prazo do histórico atualizado", extra={"event": "retention_updated",
                                                                "collection": collection,
                                                                "retention_s": self._retention_s})

    async def close(self) -> None:
        await self._client.close()

    async def save_check(self, record: dict) -> None:
        await self._checks.insert_one(dict(record))  # cópia: o pymongo acrescenta o _id no dicionário

    async def last_check(self) -> Optional[dict]:
        return await self._checks.find_one({}, {"_id": 0}, sort=[("checked_at", -1)])

    async def last_ok_check(self) -> Optional[dict]:
        return await self._checks.find_one({"status": "ok"}, {"_id": 0}, sort=[("checked_at", -1)])

    async def latest_ok_per_period(self, since: dt.datetime) -> list[dict]:
        """A verificação bem-sucedida mais recente de cada período de cobrança desde `since`."""
        cursor = await self._checks.aggregate([
            {"$match": {"status": "ok", "checked_at": {"$gte": since}}},
            {"$sort": {"checked_at": -1}},
            {"$group": {"_id": "$period.since", "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$project": {"_id": 0, "checked_at": 1, "period": 1, "daily": 1}},
        ])
        return await cursor.to_list(None)

    async def recent_checks(self, limit: int) -> list[dict]:
        cursor = self._checks.find({}, {"_id": 0, "status": 1, "checked_at": 1, "error": 1}).sort(
            "checked_at", -1).limit(limit)
        return await cursor.to_list(None)

    async def claim_notification(self, doc: dict) -> bool:
        """Cria o aviso. False se ele já existe: outra verificação já cuidou dele."""
        from pymongo.errors import DuplicateKeyError

        try:
            await self._notifications.insert_one(dict(doc))
        except DuplicateKeyError:
            return False
        return True

    async def update_notification(self, notification_id: str, fields: dict) -> None:
        await self._notifications.update_one({"_id": notification_id}, {"$set": fields})

    async def claim_retry(self, notification_id, now, lease_until, failed_before, max_attempts):
        """Reserva o aviso para uma nova tentativa, numa operação só: duas verificações não reenviam juntas."""
        from pymongo import ReturnDocument

        return await self._notifications.find_one_and_update(
            {"_id": notification_id, "attempts": {"$lt": max_attempts},
             "$or": [{"status": "failed", "updated_at": {"$lte": failed_before}},
                     {"status": "sending", "lease_until": {"$lt": now}}]},
            {"$set": {"status": "sending", "lease_until": lease_until, "updated_at": now}, "$inc": {"attempts": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def retry_candidates(self, created_since: dt.datetime, max_attempts: int) -> list[dict]:
        cursor = self._notifications.find({"status": {"$in": ["failed", "sending"]},
                                           "attempts": {"$lt": max_attempts},
                                           "created_at": {"$gte": created_since}}).sort("created_at", 1)
        return await cursor.to_list(None)

    async def notifications_for_period(self, period_since: str) -> list[dict]:
        return await self._notifications.find({"period.since": period_since}).to_list(None)

    async def list_notifications(self, limit: int) -> list[dict]:
        cursor = self._notifications.find({}).sort([("created_at", -1), ("_id", -1)]).limit(limit)
        return await cursor.to_list(None)

    async def add_client(self, doc: dict) -> None:
        from pymongo.errors import DuplicateKeyError

        try:
            await self._clients.insert_one(dict(doc))
        except DuplicateKeyError:
            raise DuplicateClient(doc["name"]) from None

    async def client_by_hash(self, key_hash: str) -> Optional[dict]:
        return await self._clients.find_one({"key_hash": key_hash}, {"_id": 0})

    async def list_clients(self) -> list[dict]:
        cursor = self._clients.find({}, {"_id": 0, "key_hash": 0}).sort("name", 1)
        return await cursor.to_list(None)

    async def set_client_active(self, name: str, active: bool) -> bool:
        result = await self._clients.update_one({"name": name}, {"$set": {"active": active}})
        return result.matched_count == 1
