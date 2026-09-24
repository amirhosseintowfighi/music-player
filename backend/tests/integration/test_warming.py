"""Warming the cache: the opening of a track, before anybody presses play.

The properties worth pinning are the ones that keep this from becoming the problem
it solves — it must fetch exactly the slice nginx would cache, ask the edge to read
it on the crawling account, never look like a play, and never warm the same track
twice while it is still warm.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import warming
from app.services.ingest import ingest_items
from tmusic_common.stream_ticket import verify

from .helpers import item, make_channel


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value


@pytest.fixture
async def resolved_track(session: AsyncSession) -> int:
    channel = await make_channel(session, "warmchan")
    await ingest_items(session, channel, [item("Dariush - Vatan", msg=1, fuid="AgADwarm1")])
    track_id = await session.scalar(
        text("SELECT id FROM tracks WHERE file_unique_id = 'AgADwarm1'")
    )
    await session.execute(
        text(
            "UPDATE tracks SET resolve_status = 'resolved', playable = true,"
            " file_size = 8000000, mime_type = 'audio/mpeg', likes_count = 99"
            " WHERE id = :id"
        ).bindparams(id=track_id)
    )
    await session.execute(
        text(
            "INSERT INTO edge_nodes (host, weight) VALUES ('https://cdn.test', 100)"
            " ON CONFLICT (host) DO UPDATE SET weight = 100, healthy = true, is_enabled = true"
        )
    )
    await session.commit()
    from app.services import stream

    stream.clear_edge_cache()
    return int(track_id)


def recorder(status: int = 206) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=b"x" * 1024)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_it_fetches_exactly_the_slice_nginx_caches(
    session: AsyncSession, resolved_track: int
) -> None:
    http, seen = recorder()
    async with http:
        result = await warming.warm_batch(session, FakeRedis(), http, get_settings(), limit=1)

    assert result["warmed"] == 1
    request = seen[0]
    assert request.url.path == f"/s/{resolved_track}"
    # One 1 MiB slice, the same range and therefore the same cache key a player asks
    # for; anything else would fill the cache with entries nobody reads.
    assert request.headers["Range"] == f"bytes=0-{warming.SLICE_BYTES - 1}"
    # And read on the crawling account, so warming never competes with listening.
    assert request.headers["X-Warm"] == "1"


async def test_a_warming_ticket_can_never_stand_in_for_a_play(
    session: AsyncSession, resolved_track: int
) -> None:
    http, seen = recorder()
    async with http:
        await warming.warm_batch(session, FakeRedis(), http, get_settings(), limit=1)

    token = seen[0].url.params["t"]
    ticket = verify(token, get_settings().signing_keys)
    assert ticket.max_bytes == warming.SLICE_BYTES  # the head of the file, nothing more
    assert ticket.user_id == 0  # nobody's play, so nobody's daily limit


async def test_a_track_already_warm_is_left_alone(
    session: AsyncSession, resolved_track: int
) -> None:
    redis = FakeRedis()
    http, seen = recorder()
    async with http:
        first = await warming.warm_batch(session, redis, http, get_settings(), limit=1)
        second = await warming.warm_batch(session, redis, http, get_settings(), limit=1)

    assert first["warmed"] == 1
    assert second["warmed"] == 0 and second["skipped"] >= 1
    assert len(seen) == 1  # the second pass asked for nothing


async def test_a_refused_fetch_is_not_remembered_as_warm(
    session: AsyncSession, resolved_track: int
) -> None:
    """A slice that never arrived must be tried again, not marked done."""
    redis = FakeRedis()
    http, _ = recorder(status=502)
    async with http:
        result = await warming.warm_batch(session, redis, http, get_settings(), limit=1)

    assert result["warmed"] == 0 and result["failed"] >= 1
    assert redis.values == {}


async def test_without_an_edge_it_does_nothing(session: AsyncSession, resolved_track: int) -> None:
    await session.execute(text("UPDATE edge_nodes SET is_enabled = false"))
    await session.commit()
    from app.services import stream

    stream.clear_edge_cache()
    http, seen = recorder()
    try:
        async with http:
            result = await warming.warm_batch(session, FakeRedis(), http, get_settings())
        assert result == {"warmed": 0, "skipped": 0, "failed": 0}
        assert seen == []
    finally:
        await session.execute(text("UPDATE edge_nodes SET is_enabled = true"))
        await session.commit()
        stream.clear_edge_cache()


async def test_it_starts_with_what_people_like(session: AsyncSession) -> None:
    """The point is the next play, so the order is the order of likely plays."""
    channel = await make_channel(session, "warmorder")
    await ingest_items(
        session,
        channel,
        [
            item("A - One", msg=11, fuid="AgADwarmA"),
            item("B - Two", msg=12, fuid="AgADwarmB"),
        ],
    )
    await session.execute(
        text(
            "UPDATE tracks SET resolve_status = 'resolved', playable = true,"
            " file_size = 8000000, likes_count = CASE WHEN file_unique_id = 'AgADwarmB'"
            " THEN 500000 ELSE 1 END WHERE file_unique_id LIKE 'AgADwarm%'"
        )
    )
    await session.commit()
    loved = await session.scalar(text("SELECT id FROM tracks WHERE file_unique_id = 'AgADwarmB'"))

    ids = list(
        await session.scalars(warming._CANDIDATES.bindparams(slice=warming.SLICE_BYTES, limit=5))
    )
    assert ids[0] == loved


def test_the_slice_matches_the_one_nginx_is_configured_for() -> None:
    """If these ever disagree, warming fills the cache with keys nobody reads."""
    import pathlib

    snippet = pathlib.Path(__file__).resolve().parents[3] / "infra/nginx/snippets/stream-cache.conf"
    assert "slice 1m;" in snippet.read_text("utf-8")
    assert warming.SLICE_BYTES == 1024 * 1024
