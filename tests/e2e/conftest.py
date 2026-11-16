"""Testes de ponta a ponta do painel: o monitor de verdade (com MapTiler simulado) num navegador de verdade.

Rodar:
    pip install -r requirements-e2e.txt && python -m playwright install chromium
    pytest tests/e2e
Com o Chrome já instalado, sem baixar o Chromium: E2E_BROWSER_CHANNEL=chrome pytest tests/e2e

Sem o Playwright instalado, estes testes são pulados (o CI principal não precisa dele).
"""
import asyncio
import datetime as dt
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402

from app.main import create_app  # noqa: E402
from app.storage import MemoryStorage  # noqa: E402
from scripts.clients import create_client  # noqa: E402
from tests.helpers import ADMIN, CHECK, KEY_A_ID, FakeAnalytics, account, fake_channels, make_settings  # noqa: E402

PERIOD_DAY = 12  # hoje é o 12º dia do período simulado


def simulated_account() -> dict:
    """12 dias de consumo terminando hoje: 78,6% da cota de requests (faixa de aviso de 70%)."""
    since = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=PERIOD_DAY - 1)
    until = since + dt.timedelta(days=29)
    days = [((since + dt.timedelta(d)).isoformat(), 5200 + 900 * (d % 4)) for d in range(PERIOD_DAY)]
    return account(days, sessions=420, since=since.isoformat(), until=until.isoformat())


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def monitor_url():
    """Sobe o monitor numa porta livre, numa thread, e faz a primeira verificação."""
    storage = MemoryStorage()

    async def add_projects():
        # Um projeto com a chave A (a de maior consumo) e um desativado, sem chaves, para o seletor de projeto.
        now = dt.datetime.now(dt.timezone.utc)
        await create_client(storage, "HydrantRouteSP", [KEY_A_ID], now)
        await create_client(storage, "Antigo", [], now)
        await storage.set_client_active("Antigo", False)

    # Numa thread própria: o Playwright já mantém um event loop nesta.
    setup = threading.Thread(target=lambda: asyncio.run(add_projects()))
    setup.start()
    setup.join()
    app = create_app(make_settings(rate_limit_per_min=10_000), storage, FakeAnalytics(simulated_account()),
                     configure_logging=False, channels=fake_channels(whatsapp=False))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("o monitor não subiu a tempo")
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    import httpx

    assert httpx.post(f"{url}/v1/checks", headers={"Authorization": f"Bearer {CHECK}"}).status_code == 200
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    channel = os.environ.get("E2E_BROWSER_CHANNEL") or None
    with sync_api.sync_playwright() as p:
        try:
            instance = p.chromium.launch(channel=channel)
        except sync_api.Error as exc:
            pytest.skip(f"navegador indisponível ({exc.message.splitlines()[0]}); "
                        "rode `python -m playwright install chromium` ou defina E2E_BROWSER_CHANNEL=chrome")
        yield instance
        instance.close()


class Problems(list):
    """Erros de JavaScript, mensagens de erro no console (inclui violações de CSP) e respostas 5xx."""


@pytest.fixture
def page(browser, request):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    problems = Problems()
    pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    pg.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
    pg.on("response", lambda r: r.status >= 500 and problems.append(f"{r.status} {r.url}"))
    pg.problems = problems
    yield pg
    context.close()


def sign_in(pg, url: str, token: str = ADMIN) -> None:
    pg.goto(f"{url}/panel")
    pg.fill("#token-input", token)
    pg.click("#login-submit")


def slow_network(pg, latency_ms: int) -> None:
    """Atrasa cada resposta em `latency_ms` (protocolo do Chrome), para reproduzir ações durante uma carga."""
    cdp = pg.context.new_cdp_session(pg)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions",
             {"offline": False, "latency": latency_ms, "downloadThroughput": -1, "uploadThroughput": -1})
