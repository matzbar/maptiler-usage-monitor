import logging

import httpx
import pytest

from app.maptiler import AnalyticsClient, AnalyticsError
from tests.helpers import SERVICE_TOKEN

pytestmark = pytest.mark.anyio

OK_BODY = {
    "since": "2026-11-14", "until": "2026-12-13",
    "legend": [{"item_id": "request.render.256", "label": "Rendered maps (256px)", "description": None}],
    "datasets": [{"group_id": "request", "item_id": "request.render.256",
                  "data": [{"date": "2026-11-14", "value": 2186}], "estimated_data": None}],
}


def make_client(responses, token=SERVICE_TOKEN):
    """responses: lista consumida a cada chamada; cada item é um httpx.Response ou uma exceção."""
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    client = AnalyticsClient(token, transport=httpx.MockTransport(handler), sleep=fake_sleep)
    return client, calls, sleeps


async def get(client, group="request"):
    try:
        return await client.timeline("services", group)
    finally:
        await client.aclose()


async def test_reads_timeline_with_token_only_in_header():
    client, calls, _ = make_client([httpx.Response(200, json=OK_BODY)])
    timeline = await get(client)
    assert timeline.datasets[0].data[0].value == 2186
    request = calls[0]
    assert request.method == "GET"
    assert str(request.url).startswith("https://service.maptiler.com/v1/analytics/api_usage/timeline?")
    assert dict(request.url.params) == {"period": "current_billing_period", "classifier": "services",
                                        "group": "request"}
    assert request.headers["authorization"] == f"Token {SERVICE_TOKEN}"
    assert SERVICE_TOKEN not in str(request.url)


@pytest.mark.parametrize("status", [401, 403])
async def test_refused_credential_is_not_retried(status):
    client, calls, sleeps = make_client([httpx.Response(status)])
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert (exc.value.kind, exc.value.http_status, len(calls), sleeps) == ("unauthorized", status, 1, [])


async def test_server_errors_are_retried_with_growing_wait():
    client, calls, sleeps = make_client([httpx.Response(500), httpx.Response(503), httpx.Response(200, json=OK_BODY)])
    assert (await get(client)).since.isoformat() == "2026-11-14"
    assert len(calls) == 3
    assert 1 <= sleeps[0] <= 1.25 and 2 <= sleeps[1] <= 2.5


async def test_timeouts_end_as_unavailable_after_three_attempts():
    client, calls, sleeps = make_client([httpx.ReadTimeout("lento")] * 3)
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "unavailable" and len(calls) == 3 and len(sleeps) == 2


async def test_network_error_is_unavailable():
    client, _, _ = make_client([httpx.ConnectError("sem rede")] * 3)
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "unavailable"


async def test_rate_limit_respects_retry_after_with_a_ceiling():
    client, _, sleeps = make_client([httpx.Response(429, headers={"Retry-After": "7"}),
                                     httpx.Response(429, headers={"Retry-After": "999"}),
                                     httpx.Response(200, json=OK_BODY)])
    await get(client)
    assert sleeps[0] >= 7 and sleeps[1] == 30


async def test_rate_limit_until_the_end_is_reported():
    client, _, _ = make_client([httpx.Response(429)] * 3)
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "rate_limited"


async def test_bad_request_carries_maptiler_message():
    body = {"errors": [{"msg": "x", "message": "Invalid request query: invalid value @ $.period"}]}
    client, calls, _ = make_client([httpx.Response(400, json=body)])
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "rejected" and "Invalid request query" in exc.value.message and len(calls) == 1


async def test_redirect_is_not_followed():
    client, calls, _ = make_client([httpx.Response(302, headers={"Location": "https://outro-site.example/"})])
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "rejected" and len(calls) == 1


@pytest.mark.parametrize("body", [
    {"until": "2026-12-13", "datasets": []},  # sem since
    {**OK_BODY, "since": "2026-12-20"},  # since depois de until
    {**OK_BODY, "datasets": [{"group_id": "request", "item_id": "x", "data": [{"date": "2026-11-14", "value": -1}]}]},
    {**OK_BODY, "datasets": [{"group_id": "request", "item_id": "x", "data": [{"date": "ontem", "value": 1}]}]},
    ["não", "é", "objeto"],
])
async def test_unexpected_format_is_invalid_response(body):
    client, _, _ = make_client([httpx.Response(200, json=body)])
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "invalid_response"


async def test_non_json_is_invalid_response():
    client, _, _ = make_client([httpx.Response(200, text="<html>manutenção</html>")])
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "invalid_response"


async def test_unexpected_group_fails_instead_of_counting_zero():
    client, _, _ = make_client([httpx.Response(200, json=OK_BODY)])
    with pytest.raises(AnalyticsError) as exc:
        await get(client, group="session")
    assert exc.value.kind == "invalid_response"


async def test_extra_fields_are_ignored():
    body = {**OK_BODY, "novo_campo": 1,
            "datasets": [{**OK_BODY["datasets"][0], "estimated_data": [{"qualquer": "formato"}], "extra": True}]}
    client, _, _ = make_client([httpx.Response(200, json=body)])
    assert (await get(client)).datasets[0].data[0].value == 2186


async def test_missing_token_makes_no_request():
    client, calls, _ = make_client([], token="")
    with pytest.raises(AnalyticsError) as exc:
        await get(client)
    assert exc.value.kind == "not_configured" and calls == []


async def test_token_never_reaches_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    client, _, _ = make_client([httpx.Response(500), httpx.Response(200, json=OK_BODY)])
    await get(client)
    client, _, _ = make_client([httpx.Response(401)])
    with pytest.raises(AnalyticsError):
        await get(client)
    assert caplog.records, "esperava logs das consultas"
    assert SERVICE_TOKEN not in caplog.text
    assert all(SERVICE_TOKEN not in str(vars(r)) for r in caplog.records)
    assert {getattr(r, "event", None) for r in caplog.records} >= {"analytics_request"}
