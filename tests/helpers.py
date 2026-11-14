"""Dados simulados para os testes: nenhum teste acessa o MapTiler de verdade."""
import datetime as dt
from typing import Optional

from app.config import Settings
from app.maptiler import Timeline

SINCE, UNTIL = "2026-11-14", "2026-12-13"
ADMIN = "a" * 40
CHECK = "c" * 40
SERVICE_TOKEN = "token-de-servico-ficticio-0123456789"
# Chaves fictícias do MapTiler (id interno e chave pública).
KEY_A_ID, KEY_A = "11111111-aaaa-4aaa-8aaa-111111111111", "AbCdEfGhIjKlMnOpQrSt"
KEY_B_ID, KEY_B = "22222222-bbbb-4bbb-8bbb-222222222222", "ZyXwVuTsRqPoNmLkJiHg"


def make_settings(**overrides) -> Settings:
    base = dict(maptiler_service_token=SERVICE_TOKEN, check_token=CHECK, admin_token=ADMIN)
    return Settings(**{**base, **overrides})


def timeline(group: str, rows: list[tuple[str, list[tuple[str, int]]]], *, legend: Optional[list[dict]] = None,
             since: str = SINCE, until: str = UNTIL) -> Timeline:
    """rows: [(item_id, [(data, valor), ...]), ...]"""
    return Timeline.model_validate({
        "since": since, "until": until, "legend": legend or [],
        "datasets": [{"group_id": group, "item_id": item, "data": [{"date": d, "value": v} for d, v in points],
                      "estimated_data": None} for item, points in rows],
    })


SERVICE_LEGEND = [{"item_id": "request.render.256", "label": "Rendered maps (256px)", "description": None}]
KEY_LEGEND = [{"item_id": KEY_A_ID, "label": KEY_A, "description": "API HydrantRouteSP"},
              {"item_id": KEY_B_ID, "label": KEY_B, "description": "Outro projeto"}]


def account(requests_by_day: Optional[list[tuple[str, int]]] = None, sessions: int = 0, *,
            since: str = SINCE, until: str = UNTIL) -> dict:
    """As quatro respostas de uma verificação. Por padrão, 2.186 requests no primeiro dia (como em 14/11/2026)."""
    days = requests_by_day or [(since, 2186)]
    total = sum(v for _, v in days)
    key_a = total - total // 10
    session_rows = [("session.maps", [(since, sessions)])] if sessions else []
    key_sessions = [(KEY_A_ID, [(since, sessions)])] if sessions else []
    kw = dict(since=since, until=until)
    return {
        ("services", "request"): timeline("request", [("request.render.256", days)], legend=SERVICE_LEGEND, **kw),
        ("services", "session"): timeline("session", session_rows, **kw),
        ("api_keys", "request"): timeline("request", [(KEY_A_ID, [(since, key_a)]), (KEY_B_ID, [(since, total - key_a)])],
                                          legend=KEY_LEGEND, **kw),
        ("api_keys", "session"): timeline("session", key_sessions, legend=KEY_LEGEND, **kw),
    }


class FakeAnalytics:
    configured = True

    def __init__(self, data: Optional[dict] = None):
        self.data = data or account()
        self.error: Optional[Exception] = None
        self.calls: list[tuple[str, str]] = []
        self.gate = None  # asyncio.Event: segura a consulta até ser liberada (testa verificação em andamento)

    async def timeline(self, classifier: str, group: str) -> Timeline:
        self.calls.append((classifier, group))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.data[(classifier, group)]

    async def aclose(self) -> None:
        return None


class Clock:
    def __init__(self, now: dt.datetime):
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += dt.timedelta(**delta)


def at(day: str, hour: int = 12) -> dt.datetime:
    return dt.datetime.fromisoformat(day).replace(hour=hour, tzinfo=dt.timezone.utc)
