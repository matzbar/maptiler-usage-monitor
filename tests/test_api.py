import asyncio

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from app.main import create_app
from app.maptiler import AnalyticsError
from app.storage import MemoryStorage
from scripts.projetos import create_client
from tests.helpers import ADMIN, CHECK, KEY_A, KEY_A_ID, SERVICE_TOKEN, Clock, FakeAnalytics, at, make_settings


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env():
    """API com banco em memória, MapTiler simulado e relógio controlado; já com um projeto cliente."""
    storage, analytics, clock = MemoryStorage(), FakeAnalytics(), Clock(at("2026-11-14"))
    client_key = asyncio.run(create_client(storage, "HydrantRouteSP", [KEY_A_ID], clock()))
    app = create_app(make_settings(), storage, analytics, clock, configure_logging=False)
    with TestClient(app) as http:
        yield http, storage, analytics, clock, client_key


def test_health_is_public_and_has_security_headers(env):
    http = env[0]
    r = http.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" not in r.headers
    assert http.get("/health", headers={"X-Forwarded-Proto": "https"}).headers["strict-transport-security"]


@pytest.mark.parametrize("headers", [{}, bearer("errado"), bearer("mum_inexistente"), {"Authorization": f"Basic {ADMIN}"}])
def test_data_routes_require_a_valid_token(env, headers):
    http = env[0]
    for method, path in (("GET", "/v1/usage"), ("GET", "/v1/usage/history"), ("POST", "/v1/checks")):
        r = http.request(method, path, headers=headers)
        assert r.status_code == 401, (method, path)
        assert r.headers["www-authenticate"] == "Bearer"


def test_each_token_only_opens_its_own_routes(env):
    http, _, _, _, client_key = env
    assert http.get("/v1/usage", headers=bearer(CHECK)).status_code == 403
    assert http.post("/v1/checks", headers=bearer(client_key)).status_code == 403


def test_scheduler_check_then_too_soon_then_admin(env):
    http, storage, _, clock, _ = env
    r = http.post("/v1/checks", headers=bearer(CHECK))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["trigger"] == "schedule"
    assert body["requests"] == {"used": 2186, "quota": 100000, "percent": 2.1}
    clock.advance(minutes=3)
    r = http.post("/v1/checks", headers=bearer(ADMIN))
    assert r.status_code == 429 and r.headers["retry-after"] == str(r.json()["retry_after_s"])
    assert r.json()["last_check"]["status"] == "ok"
    clock.advance(minutes=8)
    assert http.post("/v1/checks", headers=bearer(ADMIN)).json()["trigger"] == "manual"
    assert len(storage.checks) == 2


def test_failed_check_answers_502_and_is_recorded(env):
    http, storage, analytics, _, _ = env
    analytics.error = AnalyticsError("unauthorized", "o MapTiler recusou a credencial de serviço", 401)
    r = http.post("/v1/checks", headers=bearer(CHECK))
    assert r.status_code == 502
    assert r.json()["status"] == "failed" and r.json()["error"]["kind"] == "unauthorized"
    assert storage.checks[-1]["status"] == "failed"


def test_usage_for_client_and_admin(env):
    http, _, _, _, client_key = env
    http.post("/v1/checks", headers=bearer(CHECK))
    mine = http.get("/v1/usage", headers=bearer(client_key))
    assert mine.status_code == 200 and mine.headers["cache-control"] == "no-store"
    data = mine.json()
    assert data["status"] == "ok" and data["level"] == "normal"
    assert data["period"] == {"since": "2026-11-14", "until": "2026-12-13"}
    assert [k["key"] for k in data["keys"]] == ["AbCd…St"]
    assert data["services"][0]["label"] == "Rendered maps (256px)"
    everything = http.get("/v1/usage", headers=bearer(ADMIN)).json()
    assert len(everything["keys"]) == 2
    assert KEY_A not in mine.text + str(everything)


def test_deactivated_client_loses_access(env):
    http, storage, _, _, client_key = env
    asyncio.run(storage.set_client_active("HydrantRouteSP", False))
    assert http.get("/v1/usage", headers=bearer(client_key)).status_code == 401


def test_usage_before_any_success_has_no_numbers(env):
    http, _, analytics, _, client_key = env
    analytics.error = AnalyticsError("unavailable", "o MapTiler não respondeu dentro do tempo-limite")
    data = http.get("/v1/usage", headers=bearer(client_key)).json()
    assert data["status"] == "unavailable" and data["requests"] is None and data["level"] is None


def test_history(env):
    http, _, _, _, client_key = env
    http.post("/v1/checks", headers=bearer(CHECK))
    r = http.get("/v1/usage/history?days=7", headers=bearer(client_key))
    assert r.json() == {"days": 7, "daily": [{"date": "2026-11-14", "requests": 2186, "sessions": 0}]}
    assert http.get("/v1/usage/history?days=0", headers=bearer(client_key)).status_code == 422
    assert http.get("/v1/usage/history?days=401", headers=bearer(client_key)).status_code == 422


def test_secrets_never_appear_in_responses(env):
    http, _, analytics, clock, client_key = env
    texts = [http.post("/v1/checks", headers=bearer(CHECK)).text]
    clock.advance(minutes=11)
    analytics.error = AnalyticsError("unauthorized", "o MapTiler recusou a credencial de serviço", 401)
    texts.append(http.post("/v1/checks", headers=bearer(CHECK)).text)
    texts += [http.get(p, headers=bearer(ADMIN)).text for p in ("/v1/usage", "/v1/usage/history", "/openapi.json")]
    for text in texts:
        for secret in (SERVICE_TOKEN, ADMIN, CHECK, client_key):
            assert secret not in text


def test_docs_and_openapi(env):
    http = env[0]
    docs = http.get("/docs")
    assert docs.status_code == 200 and "cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    paths = http.get("/openapi.json").json()["paths"]
    assert {"/health", "/v1/checks", "/v1/usage", "/v1/usage/history"} <= set(paths)
    assert http.get("/redoc").status_code == 404


def test_rate_limit_per_ip():
    app = create_app(make_settings(rate_limit_per_min=3), MemoryStorage(), FakeAnalytics(),
                     Clock(at("2026-11-14")), configure_logging=False)
    with TestClient(app) as http:
        codes = [http.get("/v1/usage", headers=bearer(ADMIN)).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        assert http.get("/health").status_code == 200  # o health check não entra no limite


class BrokenStorage(MemoryStorage):
    async def last_ok_check(self):
        raise ServerSelectionTimeoutError("sem banco")


def test_database_down_answers_503():
    app = create_app(make_settings(), BrokenStorage(), FakeAnalytics(), Clock(at("2026-11-14")),
                     configure_logging=False)
    with TestClient(app) as http:
        r = http.get("/v1/usage", headers=bearer(ADMIN))
        assert r.status_code == 503 and "banco" in r.json()["detail"]
        assert http.get("/health").status_code == 200
