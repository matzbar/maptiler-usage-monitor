import datetime as dt

from app.alerts.messages import br_int, br_pct, build_message

UTC = dt.timezone.utc


def usage_doc(threshold=80, percent=81.2, used=81_234):
    return {"kind": "usage", "metric": "requests", "threshold": threshold, "percent": percent, "used": used,
            "quota": 100_000, "projection_percent": 120.0, "period": {"since": "2026-11-14", "until": "2026-12-13"},
            "check_at": dt.datetime(2026, 11, 20, 15, 0, tzinfo=UTC)}


def test_brazilian_number_format():
    assert br_int(81_234) == "81.234" and br_int(100_000) == "100.000" and br_int(5) == "5"
    assert br_pct(81.2) == "81,2%" and br_pct(100.0) == "100,0%"


def test_usage_email_and_whatsapp():
    m = build_message(usage_doc(), "America/Sao_Paulo")
    assert m.subject == "[MapTiler] 80% da cota de requests usada"
    assert "chegou a 81,2% da cota (81.234 de 100.000)" in m.text
    assert "Período de cobrança: 14/11/2026 a 13/12/2026." in m.text
    assert "Projeção até o fim do período: 120,0% da cota." in m.text
    assert "20/11/2026 às 12:00 (horário de Brasília)" in m.text  # 15:00 UTC = 12:00 em Brasília
    assert "pausa" not in m.text
    assert m.short == "*MapTiler*: requests em 81,2% da cota (81.234 de 100.000). Período até 13/12/2026. " \
                      "Projeção: 120,0%."


def test_quota_exhausted_message():
    m = build_message(usage_doc(threshold=100, percent=100.4, used=100_400), "America/Sao_Paulo")
    assert m.subject == "[MapTiler] Cota de requests esgotada (100,4%)"
    assert "pausa o serviço até o início do próximo período" in m.text


def test_monitor_down_message():
    doc = {"kind": "monitor_down", "failures": 3, "error": {"kind": "unauthorized", "message": "credencial recusada"},
           "last_ok_at": dt.datetime(2026, 11, 20, 9, 0, tzinfo=UTC)}
    m = build_message(doc, "America/Sao_Paulo")
    assert m.subject == "[MapTiler] Monitor sem dados há 3 verificações"
    assert "20/11/2026 às 06:00" in m.text and "credencial recusada (unauthorized)" in m.text
    never = build_message({**doc, "last_ok_at": None}, "UTC")
    assert "ainda não houve nenhuma verificação bem-sucedida" in never.text


def test_test_message():
    m = build_message({"kind": "test", "created_at": dt.datetime(2026, 11, 20, 15, 0, tzinfo=UTC)}, "UTC")
    assert m.subject == "[MapTiler] Teste de aviso" and "20/11/2026 às 15:00 (UTC)" in m.text
