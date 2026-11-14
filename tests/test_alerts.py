import asyncio
import datetime as dt

import pytest

from app.alerts.service import AlertService
from app.maptiler import AnalyticsError
from app.monitoring.check import Monitor
from app.storage import MemoryStorage
from tests.helpers import SINCE, Clock, FakeAnalytics, account, at, fake_channels, make_settings

pytestmark = pytest.mark.anyio


def setup(channels=None, **settings):
    clock, storage = Clock(at("2026-11-14", 0)), MemoryStorage()
    channels = channels or fake_channels()
    s = make_settings(**settings)
    monitor = Monitor(s, storage, FakeAnalytics(), clock, AlertService(s, storage, channels, clock))
    return monitor, clock, channels, storage


async def check(monitor, clock, requests=0, sessions=0, **period):
    """Avança 30 minutos e verifica com o consumo dado."""
    monitor.analytics.data = account([(period.get("since", SINCE), requests)], sessions=sessions, **period)
    monitor.analytics.error = None
    clock.advance(minutes=30)
    return (await monitor.check_now("schedule")).record


def touched(record):
    return [(n["metric"], n["threshold"], n["channel"], n["status"]) for n in record["notifications"]]


def sends(record):
    """Só os avisos de e-mail e WhatsApp (sem os registros de painel)."""
    return [t for t in touched(record) if t[2] != "panel"]


async def test_each_level_fires_once_as_usage_grows():
    m, clock, ch, _ = setup()
    assert touched(await check(m, clock, 45_000)) == []
    assert touched(await check(m, clock, 55_000)) == [("requests", 50, "panel", "recorded")]
    assert touched(await check(m, clock, 72_000)) == [("requests", 70, "panel", "recorded")]
    assert sends(await check(m, clock, 81_000)) == [("requests", 80, "email", "sent")]
    assert touched(await check(m, clock, 85_000)) == []  # mesma faixa: nada de novo
    assert touched(await check(m, clock, 91_000)) == [("requests", 90, "email", "sent"),
                                                      ("requests", 90, "whatsapp", "sent")]
    assert touched(await check(m, clock, 100_000)) == [("requests", 100, "email", "sent"),
                                                       ("requests", 100, "whatsapp", "sent")]
    assert touched(await check(m, clock, 104_000)) == []
    subjects = [msg.subject for msg, _ in ch["email"].sent]
    assert subjects == ["[MapTiler] 80% da cota de requests usada", "[MapTiler] 90% da cota de requests usada",
                        "[MapTiler] Cota de requests esgotada (100,0%)"]
    assert len(ch["whatsapp"].sent) == 2


async def test_jump_sends_only_the_highest_level():
    m, clock, ch, storage = setup()
    await check(m, clock, 60_000)
    assert touched(await check(m, clock, 92_000)) == [
        ("requests", 70, "panel", "recorded"), ("requests", 80, "email", "skipped"),
        ("requests", 90, "email", "sent"), ("requests", 90, "whatsapp", "sent")]
    assert len(ch["email"].sent) == 1
    assert storage.notifications[f"{SINCE}:requests:80:email"]["last_error"] == "superado por uma faixa mais alta"


async def test_sessions_are_evaluated_separately():
    m, clock, ch, _ = setup()
    record = await check(m, clock, requests=10_000, sessions=4_100)  # 82% das sessions
    assert ("sessions", 80, "email", "sent") in touched(record)
    assert not any(metric == "requests" for metric, *_ in touched(record))
    assert ch["email"].sent[0][0].subject == "[MapTiler] 80% da cota de sessions usada"


async def test_new_billing_period_resets_levels():
    m, clock, ch, _ = setup()
    await check(m, clock, 81_000)
    await check(m, clock, 81_000, since="2026-12-14", until="2027-01-13")
    assert [key for _, key in ch["email"].sent] == ["2026-11-14:requests:80:email", "2026-12-14:requests:80:email"]


async def test_failed_send_is_retried_on_the_next_check():
    m, clock, ch, storage = setup()
    ch["email"].fail = 1
    assert sends(await check(m, clock, 81_000)) == [("requests", 80, "email", "failed")]
    assert sends(await check(m, clock, 81_000)) == [("requests", 80, "email", "sent")]
    doc = storage.notifications[f"{SINCE}:requests:80:email"]
    assert doc["attempts"] == 2 and doc["status"] == "sent" and doc["last_error"] is None
    assert len(ch["email"].sent) == 1


async def test_retries_stop_after_max_attempts():
    m, clock, ch, storage = setup()
    ch["email"].fail = 99
    for _ in range(5):
        await check(m, clock, 81_000)
    doc = storage.notifications[f"{SINCE}:requests:80:email"]
    assert doc["attempts"] == 3 and doc["status"] == "failed" and "fora do ar" in doc["last_error"]


async def test_retry_waits_a_few_minutes_between_attempts():
    m, clock, ch, storage = setup()
    ch["email"].fail = 1
    await check(m, clock, 81_000)
    clock.advance(minutes=5)  # o intervalo mínimo entre verificações não deixa verificar; testa direto o serviço
    assert await m.alerts._retry(set()) == []
    clock.advance(minutes=6)
    assert [d["status"] for d in await m.alerts._retry(set())] == ["sent"]


async def test_failed_lower_level_is_superseded_instead_of_retried():
    m, clock, ch, storage = setup()
    ch["email"].fail = 1
    await check(m, clock, 81_000)
    record = await check(m, clock, 91_000)
    assert ("requests", 90, "email", "sent") in touched(record)
    assert ("requests", 80, "email", "skipped") in touched(record)
    assert [msg.subject for msg, _ in ch["email"].sent] == ["[MapTiler] 90% da cota de requests usada"]


async def test_unconfigured_channel_is_skipped_not_failed():
    m, clock, ch, storage = setup(channels=fake_channels(whatsapp=False))
    record = await check(m, clock, 91_000)
    assert ("requests", 90, "whatsapp", "skipped") in touched(record)
    assert storage.notifications[f"{SINCE}:requests:90:whatsapp"]["last_error"] == "canal não configurado"
    assert ch["whatsapp"].sent == [] and len(ch["email"].sent) == 1


async def test_simultaneous_processing_sends_once():
    clock, storage = Clock(at("2026-11-14")), MemoryStorage()
    s = make_settings()
    a, b = fake_channels(), fake_channels()
    first, second = AlertService(s, storage, a, clock), AlertService(s, storage, b, clock)
    m = Monitor(s, storage, FakeAnalytics(account([(SINCE, 91_000)])), clock)
    record = (await m.check_now("schedule")).record
    await asyncio.gather(first.after_check(record), second.after_check(record))
    assert len(a["email"].sent) + len(b["email"].sent) == 1
    assert len(a["whatsapp"].sent) + len(b["whatsapp"].sent) == 1


async def test_interrupted_send_is_resumed_after_the_lease():
    m, clock, ch, storage = setup()
    now = clock()
    storage.notifications["x"] = {
        "_id": "x", "kind": "usage", "channel": "email", "status": "sending", "attempts": 1,
        "created_at": now, "updated_at": now, "lease_until": now + dt.timedelta(minutes=5), "sent_at": None,
        "last_error": None, "metric": "requests", "threshold": 80, "percent": 81.0, "used": 81_000,
        "quota": 100_000, "projection_percent": 90.0, "check_at": now,
        "period": {"since": SINCE, "until": "2026-12-13"}}
    assert await m.alerts._retry(set()) == []  # ainda dentro do prazo: pode estar enviando agora
    clock.advance(minutes=6)
    assert [d["status"] for d in await m.alerts._retry(set())] == ["sent"]


async def test_monitor_down_after_three_failures_once_per_streak():
    m, clock, ch, storage = setup()
    await check(m, clock, 10_000)

    async def fail():
        m.analytics.error = AnalyticsError("unauthorized", "o MapTiler recusou a credencial de serviço", 401)
        clock.advance(minutes=30)
        return (await m.check_now("schedule")).record

    assert touched(await fail()) == [] and touched(await fail()) == []
    third = await fail()
    assert [(n["kind"], n["channel"], n["status"]) for n in third["notifications"]] == [
        ("monitor_down", "email", "sent")]
    assert touched(await fail()) == []  # mesma sequência: não repete
    await check(m, clock, 10_000)  # voltou a funcionar
    for _ in range(3):
        last = await fail()
    assert [n["kind"] for n in last["notifications"]] == ["monitor_down"]
    subjects = [msg.subject for msg, _ in ch["email"].sent]
    assert subjects == ["[MapTiler] Monitor sem dados há 3 verificações"] * 2
    assert "credencial" in ch["email"].sent[0][0].text


async def test_alert_problem_does_not_break_the_check():
    m, clock, _, storage = setup()

    async def broken(_record):
        raise RuntimeError("bug nos alertas")

    m.alerts.after_check = broken
    record = await check(m, clock, 91_000)
    assert record["status"] == "ok" and record["notifications"] == []
    assert storage.checks[-1]["status"] == "ok"


async def test_send_test_message():
    m, clock, ch, storage = setup(channels=fake_channels(whatsapp=False))
    results = await m.alerts.send_test()
    assert results == [{"channel": "email", "status": "sent", "error": None},
                       {"channel": "whatsapp", "status": "not_configured", "error": None}]
    assert ch["email"].sent[0][0].subject == "[MapTiler] Teste de aviso"
    ch["email"].fail = 1
    clock.advance(minutes=1)
    assert (await m.alerts.send_test())[0]["status"] == "failed"
    assert [d["kind"] for d in await storage.list_notifications(10)] == ["test", "test"]
