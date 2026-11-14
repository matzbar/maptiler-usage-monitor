import datetime as dt

import pytest

from app.maptiler import AnalyticsError
from app.monitoring.engine import build_snapshot, highest, level_for, mask_key, percent
from tests.helpers import KEY_A, KEY_A_ID, KEY_B_ID, SINCE, account, timeline

TODAY = dt.date(2026, 11, 14)


def snapshot(data=None, today=TODAY, quota_requests=100_000, quota_sessions=5_000):
    return build_snapshot(data or account(), quota_requests, quota_sessions, today)


def test_percent_is_truncated_never_rounded_up():
    assert percent(2186, 100_000) == 2.1
    assert percent(49_999, 100_000) == 49.9
    assert percent(100_000, 100_000) == 100.0
    assert percent(150_000, 100_000) == 150.0
    assert percent(0, 5_000) == 0.0


@pytest.mark.parametrize("used,expected", [
    (0, "normal"), (49_999, "normal"), (50_000, "notice"), (69_999, "notice"), (70_000, "warning"),
    (80_000, "high"), (89_999, "high"), (90_000, "very_high"), (100_000, "critical"), (250_000, "critical"),
])
def test_level_boundaries(used, expected):
    assert level_for(used, 100_000) == expected


def test_highest_level_wins():
    assert highest("normal", "very_high") == "very_high"
    assert highest("critical", "notice") == "critical"


def test_snapshot_from_real_shaped_data():
    s = snapshot()
    assert s["period"] == {"since": "2026-11-14", "until": "2026-12-13"}
    assert s["requests"] == {"used": 2186, "quota": 100_000, "percent": 2.1}
    assert s["sessions"] == {"used": 0, "quota": 5_000, "percent": 0.0}
    assert s["level"] == "normal"
    assert s["daily"] == [{"date": "2026-11-14", "requests": 2186, "sessions": 0}]
    assert s["by_service"] == [{"service": "request.render.256", "label": "Rendered maps (256px)",
                                "requests": 2186, "sessions": 0}]
    # Detalhe por chave: ordenado pelo consumo, com a chave pública e o nome vindos da legenda.
    assert [k["key_id"] for k in s["by_key"]] == [KEY_A_ID, KEY_B_ID]
    assert s["by_key"][0]["key_label"] == KEY_A and s["by_key"][0]["name"] == "API HydrantRouteSP"
    assert sum(k["requests"] for k in s["by_key"]) == 2186


def test_sessions_drive_the_level_when_higher():
    s = snapshot(account(sessions=4_600))
    assert s["sessions"]["percent"] == 92.0
    assert s["level"] == "very_high"


def test_daily_series_is_sorted_and_summed_per_day():
    s = snapshot(account([("2026-11-16", 300), (SINCE, 100), ("2026-11-15", 200)]), today=dt.date(2026, 11, 16))
    assert [d["date"] for d in s["daily"]] == ["2026-11-14", "2026-11-15", "2026-11-16"]
    assert s["requests"]["used"] == 600


def test_projection_uses_daily_average():
    s = snapshot(account([(SINCE, 20_000)]), today=dt.date(2026, 11, 23))  # 10º dia de 30
    assert s["projection"] == {"requests": 60_000, "requests_percent": 60.0, "sessions": 0, "sessions_percent": 0.0,
                               "based_on_days": 10, "days_in_period": 30}


def test_projection_on_first_day_and_after_the_period():
    assert snapshot(today=dt.date(2026, 11, 14))["projection"]["based_on_days"] == 1
    after = snapshot(today=dt.date(2026, 12, 20))["projection"]
    assert after["based_on_days"] == 30 and after["requests"] == 2186


def test_quota_is_configurable():
    assert snapshot(quota_requests=2_000)["level"] == "critical"


def test_mismatched_periods_are_an_error_not_a_guess():
    data = account()
    data[("services", "session")] = timeline("session", [], since="2026-10-14", until="2026-11-13")
    with pytest.raises(AnalyticsError) as exc:
        snapshot(data)
    assert exc.value.kind == "invalid_response"


def test_mask_key():
    assert mask_key("AbCdEfGhIjKlMnOpQrSt") == "AbCd…St"
    assert mask_key("curta") == "…"
    assert mask_key(None) == "…"
