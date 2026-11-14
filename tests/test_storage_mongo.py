"""Testes com um MongoDB de verdade (o CI sobe um). Localmente: MONGO_TEST_URL=mongodb://localhost:27017 pytest"""
import datetime as dt
import os
import secrets

import pytest

from app.storage import DuplicateClient, MongoStorage

URL = os.environ.get("MONGO_TEST_URL", "")
pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not URL, reason="defina MONGO_TEST_URL para testar com MongoDB")]

UTC = dt.timezone.utc


def check(day: str, hour: int, status: str = "ok", since: str = "2026-11-14") -> dict:
    record = {"checked_at": dt.datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC), "status": status,
              "trigger": "schedule", "duration_ms": 10}
    if status == "ok":
        record.update(period={"since": since, "until": "2026-12-13"},
                      daily=[{"date": day, "requests": hour, "sessions": 0}], requests={"used": hour})
    else:
        record["error"] = {"kind": "unavailable", "message": "x", "http_status": None}
    return record


@pytest.fixture
def db_name():
    return f"mum_test_{secrets.token_hex(4)}"


async def test_checks_roundtrip_indexes_and_retention(db_name):
    storage = MongoStorage(URL, db_name, retention_days=400)
    try:
        await storage.connect()
        await storage.connect()  # de novo: nada muda, nada quebra
        await storage.save_check(check("2026-10-20", 6, since="2026-10-14"))
        await storage.save_check(check("2026-11-14", 6))
        await storage.save_check(check("2026-11-14", 12))
        await storage.save_check(check("2026-11-14", 18, status="failed"))

        last = await storage.last_check()
        assert last["status"] == "failed" and "_id" not in last
        assert last["checked_at"].tzinfo is not None  # datas com fuso
        ok = await storage.last_ok_check()
        assert ok["checked_at"].hour == 12 and ok["requests"] == {"used": 12}

        per_period = await storage.latest_ok_per_period(dt.datetime(2026, 10, 1, tzinfo=UTC))
        assert sorted((c["period"]["since"], c["checked_at"].hour) for c in per_period) == [
            ("2026-10-14", 6), ("2026-11-14", 12)]

        indexes = await storage._checks.index_information()
        assert indexes["checked_at_ttl"]["expireAfterSeconds"] == 400 * 86400
        assert "status_checked_at" in indexes

        shorter = MongoStorage(URL, db_name, retention_days=30)  # mudar o prazo atualiza o índice existente
        await shorter.connect()
        assert (await storage._checks.index_information())["checked_at_ttl"]["expireAfterSeconds"] == 30 * 86400
        await shorter.close()
    finally:
        await storage._client.drop_database(db_name)
        await storage.close()


async def test_clients(db_name):
    storage = MongoStorage(URL, db_name, retention_days=400)
    try:
        await storage.connect()
        doc = {"name": "HydrantRouteSP", "key_hash": "h1", "key_prefix": "mum_abcd", "maptiler_keys": [],
               "active": True, "created_at": dt.datetime.now(UTC)}
        await storage.add_client(doc)
        with pytest.raises(DuplicateClient):
            await storage.add_client({**doc, "key_hash": "h2"})
        assert (await storage.client_by_hash("h1"))["name"] == "HydrantRouteSP"
        assert await storage.client_by_hash("outro") is None
        listed = await storage.list_clients()
        assert listed[0]["name"] == "HydrantRouteSP" and "key_hash" not in listed[0]
        assert await storage.set_client_active("HydrantRouteSP", False) is True
        assert (await storage.client_by_hash("h1"))["active"] is False
        assert await storage.set_client_active("Outro", False) is False
    finally:
        await storage._client.drop_database(db_name)
        await storage.close()
