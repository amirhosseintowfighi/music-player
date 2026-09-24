"""The forced-join gate, and what the free plan actually stops you doing.

The gate's failure mode is the interesting part: it exists to grow channels, so
every way it can go wrong — no channels configured, a name the bot cannot see,
Telegram not answering — has to let the listener in. A broken setting must not be
an outage.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import gate, plans
from app.services.ingest import ingest_items
from tests.conftest import bearer, login

from .helpers import item, make_channel


async def set_required(session: AsyncSession, names: list[str]) -> None:
    value = "[" + ", ".join(f'"{n}"' for n in names) + "]"
    await session.execute(
        text(
            "UPDATE settings SET value = CAST(:v AS jsonb) WHERE key = 'required_channels'"
        ).bindparams(v=value)
    )
    await session.commit()


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


def telegram(status: str | None = "member", ok: bool = True) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if not ok:
            return httpx.Response(400, json={"ok": False, "description": "chat not found"})
        return httpx.Response(200, json={"ok": True, "result": {"status": status}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
async def no_gate(session: AsyncSession) -> Any:
    await set_required(session, [])
    yield
    await set_required(session, [])


async def test_no_required_channels_means_no_gate(session: AsyncSession, settings: Any) -> None:
    async with telegram() as http:
        assert await gate.missing(session, FakeRedis(), http, settings, 42) == []


async def test_a_listener_who_has_not_joined_is_told_which_channel(
    session: AsyncSession, settings: Any
) -> None:
    await set_required(session, ["noax_music"])
    async with telegram(status="left") as http:
        absent = await gate.missing(session, FakeRedis(), http, settings, 42)
    assert [c.username for c in absent] == ["noax_music"]
    assert absent[0].url == "https://t.me/noax_music"


async def test_a_member_passes(session: AsyncSession, settings: Any) -> None:
    await set_required(session, ["noax_music"])
    async with telegram(status="administrator") as http:
        assert await gate.missing(session, FakeRedis(), http, settings, 42) == []


async def test_a_channel_telegram_will_not_answer_for_opens_the_gate(
    session: AsyncSession, settings: Any
) -> None:
    """The bot must be an admin of the channel. A misconfigured gate lets people in."""
    await set_required(session, ["typo_channel"])
    async with telegram(ok=False) as http:
        assert await gate.missing(session, FakeRedis(), http, settings, 42) == []


async def test_telegram_being_down_opens_the_gate(session: AsyncSession, settings: Any) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    await set_required(session, ["noax_music"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(explode)) as http:
        assert await gate.missing(session, FakeRedis(), http, settings, 42) == []


async def test_the_answer_is_cached_and_recheck_clears_it(
    session: AsyncSession, settings: Any
) -> None:
    """A tap on "I joined" must be believed at once, not in five minutes."""
    await set_required(session, ["noax_music"])
    redis = FakeRedis()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True, "result": {"status": "left"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await gate.missing(session, redis, http, settings, 42)
        await gate.missing(session, redis, http, settings, 42)
        assert calls == 1  # the second answer came from the cache

        await gate.forget(redis, 42)
        await gate.missing(session, redis, http, settings, 42)
        assert calls == 2


# ── free plan limits ──────────────────────────────────────────────────────────


async def test_the_free_plan_ships_the_limits_the_owner_asked_for(
    session: AsyncSession,
) -> None:
    plans.clear_caches()
    free = await plans.get_plan(session, "free")
    assert free.limit("daily_plays") == 40
    assert free.limit("playlists") == 2
    assert free.limit("channels") == 1
    assert free.limit("library") == 200
    assert free.limits["download"] is False


async def test_premium_has_no_ceiling_anywhere(session: AsyncSession) -> None:
    plans.clear_caches()
    pro = await plans.get_plan(session, "pro_monthly")
    for name in ("daily_plays", "playlists", "channels", "library"):
        assert pro.limit(name) == plans.UNLIMITED, name
    assert pro.limits["download"] is True


async def test_a_free_listener_cannot_keep_more_than_the_library_limit(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The cap is on what you keep, so re-liking something you kept is not blocked."""
    await session.execute(
        text("UPDATE plans SET limits = limits || '{\"library\": 2}'::jsonb WHERE code = 'free'")
    )
    await session.commit()
    plans.clear_caches()
    channel = await make_channel(session, "lib_limit")
    await ingest_items(
        session,
        channel,
        [
            item("A - One", msg=1, fuid="AgADlib1"),
            item("B - Two", msg=2, fuid="AgADlib2"),
            item("C - Three", msg=3, fuid="AgADlib3"),
        ],
    )
    await session.commit()
    ids = [
        row[0]
        for row in (
            await session.execute(
                text("SELECT id FROM tracks WHERE file_unique_id LIKE 'AgADlib%' ORDER BY id")
            )
        ).all()
    ]
    auth = bearer(await login(client, 7788))
    try:
        assert (await client.put(f"/v1/tracks/{ids[0]}/like", headers=auth)).status_code == 200
        assert (await client.put(f"/v1/tracks/{ids[1]}/like", headers=auth)).status_code == 200

        refused = await client.put(f"/v1/tracks/{ids[2]}/like", headers=auth)
        assert refused.status_code == 402
        assert refused.json()["error"]["details"]["kind"] == "library"

        # Already kept: not one more, so it still works.
        assert (await client.put(f"/v1/tracks/{ids[0]}/like", headers=auth)).status_code == 200
    finally:
        await session.execute(
            text(
                "UPDATE plans SET limits = limits || '{\"library\": 200}'::jsonb"
                " WHERE code = 'free'"
            )
        )
        await session.commit()
        plans.clear_caches()
