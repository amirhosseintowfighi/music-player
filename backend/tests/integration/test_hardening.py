"""Phase 9: maintenance mode, metrics, security headers and the performance guards.

These are the properties that only show up under load or during an incident, so they
get explicit tests rather than trust.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.services import plans
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe
from tests.integration.test_admin import OWNER_TG, admin_token, make_admin


def counter_value(counter: Any, **labels: str) -> float:
    """Reads one Prometheus counter sample, or 0 when it has never been touched."""
    child = counter.labels(**labels) if labels else counter
    return float(child._value.get())


# ── maintenance mode ──────────────────────────────────────────────────────────


@pytest.fixture
async def maintenance(session: AsyncSession) -> Any:
    await session.execute(
        text("UPDATE feature_flags SET value = 'true' WHERE key = 'maintenance_mode'")
    )
    await session.commit()
    plans.clear_caches()
    yield
    await session.rollback()
    await session.execute(
        text("UPDATE feature_flags SET value = 'false' WHERE key = 'maintenance_mode'")
    )
    await session.commit()
    plans.clear_caches()


async def test_maintenance_mode_stops_user_traffic(
    client: httpx.AsyncClient, session: AsyncSession, maintenance: None
) -> None:
    resp = await client.get("/v1/library/tracks")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "unavailable"


async def test_maintenance_mode_keeps_health_and_admin_working(
    client: httpx.AsyncClient, session: AsyncSession, maintenance: None
) -> None:
    await make_admin(session, OWNER_TG)
    await session.commit()
    assert (await client.get("/healthz")).status_code == 200
    headers = await admin_token(client, OWNER_TG)
    assert (await client.get("/admin/overview", headers=headers)).status_code == 200


async def test_normal_traffic_resumes_when_the_flag_is_off(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    token = await login(client, 70001)
    assert (await client.get("/v1/me", headers=bearer(token))).status_code == 200


# ── metrics ───────────────────────────────────────────────────────────────────


async def test_metrics_endpoint_exposes_http_and_business_series(
    client: httpx.AsyncClient,
) -> None:
    await client.get("/healthz")
    body = (await client.get("/metrics")).text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "plays_total" in body
    assert "payments_total" in body
    assert "active_subscriptions" in body


async def test_a_play_increments_the_play_counter(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "metricsch")
    await ingest_items(session, channel, [item("آهنگ", "معین", msg=1)], bot_id=None)
    row = await session.execute(text("SELECT id FROM tracks LIMIT 1"))
    track_id = row.scalar_one()
    token = await login(client, 70002)
    await subscribe(session, token["me"]["id"], channel.id)
    await session.commit()

    before = counter_value(metrics.PLAYS, source="library")
    resp = await client.post(
        "/v1/history",
        json={"track_id": track_id, "duration_played": 200, "completed": True, "source": "library"},
        headers=bearer(token),
    )
    assert resp.status_code == 204
    assert counter_value(metrics.PLAYS, source="library") == before + 1


async def test_a_settled_payment_increments_revenue(
    session: AsyncSession, paid_settings: Any
) -> None:
    from app.services import subscriptions
    from tests.integration.test_payments import ZARINPAL, enable, gateway, make_user

    await enable(session, "zarinpal")
    user = await make_user(session, tg_id=70003)
    before = counter_value(metrics.REVENUE, provider="zarinpal", currency="IRR")
    async with gateway(ZARINPAL) as http:
        await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal"
        )
        await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", {"Authority": "A1", "Status": "OK"}
        )
    assert counter_value(metrics.REVENUE, provider="zarinpal", currency="IRR") == before + 1_490_000
    assert counter_value(metrics.PAYMENTS, provider="zarinpal", status="paid") >= 1
    plans.clear_caches()


async def test_the_metrics_gauge_job_runs(session: AsyncSession, engine: Any) -> None:
    import fakeredis

    from app.db import make_sessionmaker
    from app.workers import jobs

    await login_free_user(session)
    ctx = {
        "sessionmaker": make_sessionmaker(engine),
        "redis_app": fakeredis.aioredis.FakeRedis(decode_responses=True),
    }
    assert await jobs.export_business_metrics(ctx) == {"exported": 1}
    assert metrics.USERS_TOTAL._value.get() >= 1


async def login_free_user(session: AsyncSession) -> None:
    from app.models import User

    session.add(User(tg_id=70004, first_name="M", referral_code="rm70004"))
    await session.commit()


# ── security ──────────────────────────────────────────────────────────────────


async def test_security_headers_are_always_present(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-request-id"]


async def test_a_forged_trace_header_is_not_reflected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert "<script>" not in resp.headers["x-request-id"]


async def test_rate_limit_applies_per_user(
    client: httpx.AsyncClient, session: AsyncSession, app_state: Any
) -> None:
    app_state.settings = app_state.settings.model_copy(update={"rate_limit_user_per_min": 3})
    token = await login(client, 70005)
    statuses = [(await client.get("/v1/me", headers=bearer(token))).status_code for _ in range(6)]
    assert 429 in statuses
    assert statuses.count(200) <= 3
    limited = await client.get("/v1/me", headers=bearer(token))
    assert limited.json()["error"]["code"] == "rate_limited"
    app_state.settings = app_state.settings.model_copy(update={"rate_limit_user_per_min": 240})


async def test_unknown_routes_do_not_leak_internals(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/does-not-exist")
    assert resp.status_code == 404
    assert "Traceback" not in resp.text


async def test_validation_errors_are_shaped_like_every_other_error(
    client: httpx.AsyncClient,
) -> None:
    token = await login(client, 70006)
    resp = await client.get("/v1/library/tracks?limit=99999", headers=bearer(token))
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "invalid_input"


# ── performance guards ────────────────────────────────────────────────────────


async def test_library_listing_is_constant_query_count(
    client: httpx.AsyncClient, session: AsyncSession, count_queries: Any
) -> None:
    """The N+1 guard: 5 tracks and 40 tracks must cost the same number of queries."""
    channel = await make_channel(session, "perfch")
    await ingest_items(
        session,
        channel,
        [item(f"ترک {i}", f"خواننده {i % 5}", msg=500 + i) for i in range(5)],
        bot_id=None,
    )
    token = await login(client, 70007)
    await subscribe(session, token["me"]["id"], channel.id)
    await session.commit()

    with count_queries() as small:
        await client.get("/v1/library/tracks?limit=50", headers=bearer(token))

    await ingest_items(
        session,
        channel,
        [item(f"ترک تازه {i}", f"خواننده {i % 5}", msg=600 + i) for i in range(35)],
        bot_id=None,
    )
    await session.commit()

    with count_queries() as large:
        resp = await client.get("/v1/library/tracks?limit=50", headers=bearer(token))
    assert len(resp.json()["items"]) == 40
    assert large.count <= small.count, large.statements


async def test_pagination_is_cursor_based_everywhere(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """No OFFSET in list endpoints: the second page must not re-scan the first."""
    channel = await make_channel(session, "pagech")
    await ingest_items(
        session,
        channel,
        [item(f"صفحه {i}", "معین", msg=700 + i) for i in range(12)],
        bot_id=None,
    )
    token = await login(client, 70008)
    await subscribe(session, token["me"]["id"], channel.id)
    await session.commit()

    first = await client.get("/v1/library/tracks?limit=5", headers=bearer(token))
    body = first.json()
    assert len(body["items"]) == 5
    assert body["next_cursor"]

    second = await client.get(
        f"/v1/library/tracks?limit=5&cursor={body['next_cursor']}", headers=bearer(token)
    )
    ids_first = {track["id"] for track in body["items"]}
    ids_second = {track["id"] for track in second.json()["items"]}
    assert not (ids_first & ids_second)  # no overlap, no duplicates


async def test_an_invalid_cursor_is_rejected_not_ignored(client: httpx.AsyncClient) -> None:
    token = await login(client, 70009)
    resp = await client.get("/v1/library/tracks?cursor=not-a-cursor", headers=bearer(token))
    assert resp.status_code in (400, 422)
