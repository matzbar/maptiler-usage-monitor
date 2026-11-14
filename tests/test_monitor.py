import asyncio

import pytest

from app.maptiler import AnalyticsError
from app.monitoring.check import QUERIES, Monitor
from app.storage import MemoryStorage
from tests.helpers import KEY_A, KEY_A_ID, Clock, FakeAnalytics, account, at, make_settings

pytestmark = pytest.mark.anyio


def make_monitor(clock=None, analytics=None, storage=None, **settings):
    clock = clock or Clock(at("2026-11-14"))
    return Monitor(make_settings(**settings), storage or MemoryStorage(), analytics or FakeAnalytics(), clock)


async def test_successful_check_is_saved_with_all_queries():
    m = make_monitor()
    result = await m.check_now("schedule")
    assert result.outcome == "done"
    record = result.record
    assert record["status"] == "ok" and record["trigger"] == "schedule" and record["period_changed"] is False
    assert record["requests"]["used"] == 2186 and record["level"] == "normal"
    assert sorted(m.analytics.calls) == sorted(QUERIES)
    assert m.storage.checks[0]["status"] == "ok" and "duration_ms" in m.storage.checks[0]


async def test_failure_is_recorded_and_never_read_as_zero():
    clock = Clock(at("2026-11-14", 6))
    m = make_monitor(clock)
    await m.check_now("schedule")
    clock.advance(hours=1)
    m.analytics.error = AnalyticsError("unavailable", "o MapTiler não respondeu dentro do tempo-limite")
    failed = (await m.check_now("schedule")).record
    assert failed["status"] == "failed" and failed["error"]["kind"] == "unavailable"
    assert "requests" not in failed  # nada de consumo zero numa falha
    usage = await m.usage(None)
    assert usage["status"] == "ok"
    assert usage["requests"]["used"] == 2186  # continua o último valor válido...
    assert usage["checked_at"] == at("2026-11-14", 6)  # ...com a data dele
    assert usage["last_error"]["kind"] == "unavailable"


async def test_unavailable_when_no_check_ever_succeeded():
    m = make_monitor()
    m.analytics.error = AnalyticsError("unauthorized", "o MapTiler recusou a credencial de serviço", 401)
    await m.check_now("schedule")
    usage = await m.usage(None)
    assert usage == {"status": "unavailable", "checked_at": None,
                     "last_error": {"checked_at": at("2026-11-14"), "kind": "unauthorized",
                                    "message": "o MapTiler recusou a credencial de serviço"}}


async def test_unexpected_exception_is_recorded_as_internal_failure():
    m = make_monitor()
    m.analytics.error = RuntimeError("bug")
    record = (await m.check_now("manual")).record
    assert record["status"] == "failed" and record["error"]["kind"] == "internal"
    assert "bug" not in record["error"]["message"]


async def test_minimum_interval_between_checks():
    clock = Clock(at("2026-11-14"))
    m = make_monitor(clock)
    await m.check_now("schedule")
    clock.advance(minutes=4)
    too_soon = await m.check_now("schedule")
    assert too_soon.outcome == "too_soon" and 359 <= too_soon.retry_after_s <= 361
    assert len(m.storage.checks) == 1
    clock.advance(minutes=7)
    assert (await m.check_now("schedule")).outcome == "done"


async def test_only_one_check_at_a_time():
    m = make_monitor()
    m.analytics.gate = asyncio.Event()
    first = asyncio.create_task(m.check_now("schedule"))
    await asyncio.sleep(0)
    assert (await m.check_now("manual")).outcome == "busy"
    m.analytics.gate.set()
    assert (await first).outcome == "done"


async def test_billing_period_change_is_detected():
    clock = Clock(at("2026-11-13"))
    old = account(since="2026-10-14", until="2026-11-13")
    m = make_monitor(clock, FakeAnalytics(old))
    assert (await m.check_now("schedule")).record["period_changed"] is False
    clock.advance(days=1)
    m.analytics.data = account()
    record = (await m.check_now("schedule")).record
    assert record["period_changed"] is True and record["period"]["since"] == "2026-11-14"


async def test_stale_after_twelve_hours_and_after_period_end():
    clock = Clock(at("2026-11-14", 0))
    m = make_monitor(clock, refresh_after_s=10**9)  # sem atualização sob demanda: só a regra de idade
    await m.check_now("schedule")
    clock.advance(hours=11)
    assert (await m.usage(None))["status"] == "ok"
    clock.advance(hours=2)
    assert (await m.usage(None))["status"] == "stale"
    await m.aclose()

    clock = Clock(at("2026-12-13", 20))
    m = make_monitor(clock)
    await m.check_now("schedule")
    clock.advance(hours=5)  # 14/12: o período guardado (até 13/12) acabou
    assert (await m.usage(None))["status"] == "stale"
    await m.aclose()


async def test_old_data_triggers_a_background_check_on_read():
    clock = Clock(at("2026-11-14", 0))
    m = make_monitor(clock)
    await m.check_now("schedule")
    clock.advance(hours=5)
    await m.usage(None)
    await m.wait_background()
    assert len(m.storage.checks) == 1  # dado de 5 horas: não verifica de novo
    clock.advance(hours=2)
    usage = await m.usage(None)
    assert usage["status"] == "ok"  # responde na hora com o dado guardado...
    await m.wait_background()
    assert [c["trigger"] for c in m.storage.checks] == ["schedule", "on_demand"]  # ...e atualiza por trás


async def test_first_read_triggers_a_check():
    m = make_monitor()
    assert (await m.usage(None))["status"] == "unavailable"
    await m.wait_background()
    assert (await m.usage(None))["status"] == "ok"


async def test_client_sees_only_its_own_keys_masked():
    m = make_monitor()
    await m.check_now("schedule")
    mine = await m.usage(frozenset({KEY_A_ID}))
    assert [k["key"] for k in mine["keys"]] == ["AbCd…St"]
    by_label = await m.usage(frozenset({KEY_A}))
    assert [k["key"] for k in by_label["keys"]] == ["AbCd…St"]
    assert len((await m.usage(None))["keys"]) == 2
    assert (await m.usage(frozenset()))["keys"] == []
    assert KEY_A not in str(await m.usage(None))  # a chave completa nunca sai na resposta


async def test_history_joins_billing_periods():
    clock = Clock(at("2026-11-13", 12))
    m = make_monitor(clock, FakeAnalytics(account([("2026-11-12", 50), ("2026-11-13", 70)],
                                                  since="2026-10-14", until="2026-11-13")))
    await m.check_now("schedule")
    clock.advance(days=2)
    m.analytics.data = account([("2026-11-14", 100), ("2026-11-15", 200)])
    await m.check_now("schedule")
    assert await m.history(4) == [
        {"date": "2026-11-12", "requests": 50, "sessions": 0},
        {"date": "2026-11-13", "requests": 70, "sessions": 0},
        {"date": "2026-11-14", "requests": 100, "sessions": 0},
        {"date": "2026-11-15", "requests": 200, "sessions": 0},
    ]
    assert [d["date"] for d in await m.history(2)] == ["2026-11-14", "2026-11-15"]
