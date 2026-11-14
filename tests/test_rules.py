import pytest

from app.alerts.rules import Planned, plan
from app.config import DEFAULT_ALERT_RULES, parse_alert_rules

RULES = parse_alert_rules(DEFAULT_ALERT_RULES)


def actions(used, quota=100_000):
    return {(p.threshold, p.channel): p.action for p in plan(used, quota, RULES)}


def test_below_fifty_percent_nothing_happens():
    assert plan(49_999, 100_000, RULES) == []


def test_fifty_and_seventy_only_record_for_the_panel():
    assert actions(50_000) == {(50, "panel"): "record"}
    assert actions(72_000) == {(50, "panel"): "record", (70, "panel"): "record"}


def test_eighty_sends_email():
    assert actions(80_000)[(80, "email")] == "notify"


def test_ninety_sends_email_and_whatsapp_and_supersedes_eighty():
    a = actions(92_000)
    assert a[(90, "email")] == "notify" and a[(90, "whatsapp")] == "notify"
    assert a[(80, "email")] == "skip"


def test_hundred_is_the_top():
    a = actions(100_000)
    assert a[(100, "email")] == a[(100, "whatsapp")] == "notify"
    assert a[(90, "email")] == a[(90, "whatsapp")] == a[(80, "email")] == "skip"


def test_works_with_sessions_quota():
    assert actions(4_000, quota=5_000)[(80, "email")] == "notify"


def test_custom_rules():
    rules = parse_alert_rules("60:email, 95:whatsapp+email")
    assert plan(96, 100, rules) == [Planned(60, "email", "skip"), Planned(95, "email", "notify"),
                                    Planned(95, "whatsapp", "notify")]


@pytest.mark.parametrize("raw", ["80", "80:sms", "x:email", "0:email", "80:email,80:panel"])
def test_invalid_rules(raw):
    with pytest.raises(ValueError, match="ALERT_RULES"):
        parse_alert_rules(raw)
