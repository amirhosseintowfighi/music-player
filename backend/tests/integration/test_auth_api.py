from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.conftest import bearer, init_data_for, login


async def test_login_creates_user_and_returns_tokens(client: httpx.AsyncClient) -> None:
    body = await login(client, 555)
    assert body["token_type"] == "bearer"
    me = body["me"]
    assert me["tg_id"] == 555
    assert me["plan"] == "free"
    assert me["lang"] == "fa"
    assert me["limits"]["channels"] == 1
    assert "discover_weekly" in me["features"]

    resp = await client.get("/v1/me", headers=bearer(body))
    assert resp.status_code == 200
    assert resp.json()["id"] == me["id"]
    assert resp.headers["x-request-id"]
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_login_is_idempotent_and_updates_profile(client: httpx.AsyncClient) -> None:
    first = await login(client, 556)
    resp = await client.post(
        "/v1/auth/telegram", json={"init_data": init_data_for(556, first_name="Renamed")}
    )
    assert resp.status_code == 200
    assert resp.json()["me"]["id"] == first["me"]["id"]
    assert resp.json()["me"]["first_name"] == "Renamed"


async def test_start_param_is_passed_through(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/telegram", json={"init_data": init_data_for(557, start_param="pl_x1")}
    )
    assert resp.json()["start_param"] == "pl_x1"


async def test_login_rejects_forged_init_data(client: httpx.AsyncClient) -> None:
    forged = init_data_for(558).replace("Test", "Evil")
    resp = await client.post("/v1/auth/telegram", json={"init_data": forged})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_validation_errors_use_the_error_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/auth/telegram", json={"init_data": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_input"


async def test_banned_user_cannot_login(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await login(client, 559)
    await session.execute(update(User).where(User.tg_id == 559).values(is_banned=True))
    await session.commit()
    resp = await client.post("/v1/auth/telegram", json={"init_data": init_data_for(559)})
    assert resp.status_code == 403


async def test_refresh_rotates_and_detects_reuse(client: httpx.AsyncClient) -> None:
    body = await login(client, 560)
    old = body["refresh_token"]
    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": old})
    assert rotated.status_code == 200
    new = rotated.json()["refresh_token"]
    assert new != old

    reuse = await client.post("/v1/auth/refresh", json={"refresh_token": old})
    assert reuse.status_code == 401
    # The whole family is revoked, including the token issued by the rotation.
    after = await client.post("/v1/auth/refresh", json={"refresh_token": new})
    assert after.status_code == 401


async def test_logout_revokes_refresh(client: httpx.AsyncClient) -> None:
    body = await login(client, 561)
    assert (
        await client.post("/v1/auth/logout", json={"refresh_token": body["refresh_token"]})
    ).status_code == 204
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 401


async def test_expired_refresh_is_rejected(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    body = await login(client, 562)
    await session.execute(
        text("UPDATE refresh_tokens SET expires_at = :t").bindparams(
            t=datetime.now(UTC) - timedelta(seconds=1)
        )
    )
    await session.commit()
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 401


async def test_refresh_for_banned_user(client: httpx.AsyncClient, session: AsyncSession) -> None:
    body = await login(client, 563)
    await session.execute(update(User).where(User.tg_id == 563).values(is_banned=True))
    await session.commit()
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 403


async def test_paid_plan_only_while_valid(client: httpx.AsyncClient, session: AsyncSession) -> None:
    body = await login(client, 564)
    await session.execute(
        update(User)
        .where(User.tg_id == 564)
        .values(plan_code="pro_monthly", premium_until=datetime.now(UTC) + timedelta(days=3))
    )
    await session.commit()
    pro = await client.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert pro.json()["me"]["plan"] == "pro_monthly"
    assert pro.json()["me"]["limits"]["channels"] == -1

    await session.execute(
        update(User)
        .where(User.tg_id == 564)
        .values(premium_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    lapsed = await client.post(
        "/v1/auth/refresh", json={"refresh_token": pro.json()["refresh_token"]}
    )
    assert lapsed.json()["me"]["plan"] == "free"


async def test_protected_routes_need_a_valid_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/me")).status_code == 401
    bad = await client.get("/v1/me", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


async def test_set_language(client: httpx.AsyncClient) -> None:
    body = await login(client, 565)
    resp = await client.patch("/v1/me/lang", json={"lang": "en"}, headers=bearer(body))
    assert resp.status_code == 200
    assert resp.json()["lang"] == "en"


async def test_banned_set_blocks_existing_tokens(client: httpx.AsyncClient, redis) -> None:  # type: ignore[no-untyped-def]
    body = await login(client, 566)
    await redis.sadd("banned_users", str(body["me"]["id"]))
    assert (await client.get("/v1/me", headers=bearer(body))).status_code == 403


async def test_user_rate_limit(client: httpx.AsyncClient, settings, redis) -> None:  # type: ignore[no-untyped-def]
    body = await login(client, 567)
    import time

    await redis.set(
        f"rl:u:{body['me']['id']}:{int(time.time() // 60)}", settings.rate_limit_user_per_min
    )
    resp = await client.get("/v1/me", headers=bearer(body))
    assert resp.status_code == 429
    assert "retry-after" in resp.headers


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["db"] is True
    metrics = await client.get("/metrics")
    assert "http_requests_total" in metrics.text


async def test_incoming_trace_id_is_echoed(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "abcdef0123456789"})
    assert resp.headers["x-request-id"] == "abcdef0123456789"
    bad = await client.get("/healthz", headers={"X-Request-ID": "<script>"})
    assert bad.headers["x-request-id"] != "<script>"
