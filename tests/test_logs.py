import json
import logging

from app.logs import JsonFormatter


def render(**extra) -> dict:
    record = logging.LogRecord("monitor.check", logging.INFO, __file__, 1, "verificação concluída", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))


def test_one_json_object_per_event_with_extra_fields():
    data = render(event="check_finished", duration_ms=318, usage_level="normal")
    assert data["level"] == "info" and data["logger"] == "monitor.check" and data["msg"] == "verificação concluída"
    assert data["event"] == "check_finished" and data["duration_ms"] == 318 and data["usage_level"] == "normal"
    assert data["ts"].endswith("+00:00")


def test_extra_field_cannot_overwrite_base_fields():
    data = render(level="normal", msg_extra="x")
    assert data["level"] == "info" and data["extra_level"] == "normal"
