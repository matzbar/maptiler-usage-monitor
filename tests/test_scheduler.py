"""scripts/scheduler.py: o que o GitHub Actions executa. O monitor é simulado com httpx.MockTransport."""
import httpx
import pytest

from scripts.scheduler import WAKE_ATTEMPTS, main

URL, TOKEN = "https://monitor.example.com", "c" * 40
ENV = {"MONITOR_URL": URL, "MONITOR_CHECK_TOKEN": TOKEN}
CHECK_OK = {"status": "ok", "requests": {"used": 78600, "quota": 100000, "percent": 78.6},
            "sessions": {"used": 420, "quota": 5000, "percent": 8.4}, "level": "warning"}


class FakeMonitor:
    """Responde por rota; guarda as chamadas. `health_failures` = quantas vezes /health falha antes de responder."""

    def __init__(self, routes=None, health_failures=0):
        self.routes = {"POST /v1/checks": (200, CHECK_OK),
                       "POST /v1/reports/daily": (200, {"status": "sent", "date": "2026-11-15", "error": None}),
                       **(routes or {})}
        self.health_failures = health_failures
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        self.calls.append((key, request.headers.get("authorization")))
        if key == "GET /health":
            if self.health_failures:
                self.health_failures -= 1
                raise httpx.ConnectError("serviço acordando")
            return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "commit": None})
        status, body = self.routes[key]
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json=body)

    def posts(self):
        return [key for key, _ in self.calls if key.startswith("POST")]


def run(monitor, *tasks, env=ENV, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return main(list(tasks), env=env, transport=httpx.MockTransport(monitor), sleep=sleeps.append)


def test_check_ok_sends_the_token_once_per_route(capsys):
    monitor = FakeMonitor()
    assert run(monitor, "check") == 0
    assert monitor.posts() == ["POST /v1/checks"]
    assert all(auth == f"Bearer {TOKEN}" for _, auth in monitor.calls)
    out = capsys.readouterr().out
    assert "check: ok — verificação feita: requests em 78,6%, sessions em 8,4% (faixa warning)" in out
    assert TOKEN not in out


def test_morning_run_checks_then_sends_the_report_in_order():
    monitor = FakeMonitor()
    assert run(monitor, "check", "report", "check") == 0  # repetida na linha de comando, roda uma vez só
    assert monitor.posts() == ["POST /v1/checks", "POST /v1/reports/daily"]


@pytest.mark.parametrize("status, body", [
    (429, {"detail": "recente", "retry_after_s": 300, "last_check": CHECK_OK}),
    (409, {"detail": "já há uma verificação em andamento"}),
])
def test_recent_or_running_check_is_not_a_failure(status, body):
    monitor = FakeMonitor({"POST /v1/checks": (status, body)})
    assert run(monitor, "check") == 0
    assert monitor.posts() == ["POST /v1/checks"]  # nunca repete a verificação


@pytest.mark.parametrize("status, body, expected", [
    (502, {"status": "failed", "error": {"kind": "unauthorized", "message": "credencial recusada"}},
     "a consulta ao MapTiler falhou (unauthorized): credencial recusada"),
    (401, {"detail": "token ausente ou inválido"}, "confira o segredo MONITOR_CHECK_TOKEN"),
    (403, {"detail": "este token não dá acesso"}, "confira o segredo MONITOR_CHECK_TOKEN"),
    (429, {"detail": "muitas requisições"}, "resposta inesperada do monitor (HTTP 429): muitas requisições"),
    (503, {"detail": "banco de dados indisponível"}, "HTTP 503"),
])
def test_failures_turn_the_job_red_without_retrying(status, body, expected, capsys):
    monitor = FakeMonitor({"POST /v1/checks": (status, body)})
    assert run(monitor, "check", env={**ENV, "GITHUB_ACTIONS": "true"}) == 1
    assert monitor.posts() == ["POST /v1/checks"]
    out = capsys.readouterr().out
    assert expected in out
    assert "::error title=Monitor MapTiler (check)::" in out  # anotação no GitHub Actions


def test_a_failed_check_does_not_stop_the_report():
    monitor = FakeMonitor({"POST /v1/checks": (502, {"error": {"kind": "unavailable", "message": "fora do ar"}})})
    assert run(monitor, "check", "report") == 1
    assert monitor.posts() == ["POST /v1/checks", "POST /v1/reports/daily"]


@pytest.mark.parametrize("body, code, text", [
    ({"status": "already_sent", "date": "2026-11-15", "error": None}, 0, "já tinha sido enviado"),
    ({"status": "not_configured", "date": "2026-11-15", "error": None}, 0, "e-mail não configurado"),
    ({"status": "failed", "date": "2026-11-15", "error": "Resend fora do ar"}, 1, "Resend fora do ar"),
])
def test_report_outcomes(body, code, text, capsys):
    monitor = FakeMonitor({"POST /v1/reports/daily": (200, body)})
    assert run(monitor, "report") == code
    assert text in capsys.readouterr().out


def test_waits_for_a_sleeping_monitor_to_wake_up():
    monitor, sleeps = FakeMonitor(health_failures=3), []
    assert run(monitor, "check", sleeps=sleeps) == 0
    assert len(sleeps) == 3 and monitor.posts() == ["POST /v1/checks"]


def test_monitor_that_never_wakes_up_fails_without_calling_the_routines(capsys):
    monitor, sleeps = FakeMonitor(health_failures=99), []
    assert run(monitor, "check", "report", sleeps=sleeps) == 1
    assert monitor.posts() == []
    assert len(sleeps) == WAKE_ATTEMPTS - 1
    assert f"não respondeu em /health depois de {WAKE_ATTEMPTS} tentativas (ConnectError)" in capsys.readouterr().out


def test_network_error_on_a_routine_is_a_failure(capsys):
    monitor = FakeMonitor({"POST /v1/checks": (httpx.ReadTimeout("lento"), None)})
    assert run(monitor, "check") == 1
    assert "sem resposta do monitor (ReadTimeout)" in capsys.readouterr().out


@pytest.mark.parametrize("env", [{}, {"MONITOR_URL": URL}, {"MONITOR_CHECK_TOKEN": TOKEN},
                                 {"MONITOR_URL": " ", "MONITOR_CHECK_TOKEN": TOKEN}])
def test_without_secrets_the_schedule_is_off_and_green(env, capsys):
    monitor = FakeMonitor()
    assert run(monitor, "check", env={**env, "GITHUB_ACTIONS": "true"}) == 0
    assert monitor.calls == []
    assert "::notice title=Monitor MapTiler::agendamento desligado" in capsys.readouterr().out


@pytest.mark.parametrize("url, code", [
    ("http://monitor.example.com", 1),  # token sem criptografia: recusado antes de qualquer chamada
    ("ftp://monitor.example.com", 1),
    ("monitor.example.com", 1),
    ("http://localhost:8000", 0),
    ("https://monitor.example.com/", 0),  # barra final é tolerada
])
def test_only_https_outside_localhost(url, code):
    monitor = FakeMonitor()
    assert run(monitor, "check", env={**ENV, "MONITOR_URL": url}) == code
    assert bool(monitor.calls) == (code == 0)


def test_writes_the_github_step_summary(tmp_path):
    summary = tmp_path / "summary.md"
    env = {**ENV, "GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}
    assert run(FakeMonitor(), "check", "report", env=env) == 0
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("### Monitor MapTiler")
    assert "- check: ok" in text and "- report: ok — relatório de 2026-11-15 enviado" in text
    assert TOKEN not in text


def test_unknown_task_is_rejected():
    with pytest.raises(SystemExit):
        main(["limpar"], env=ENV)
