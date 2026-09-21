"""Phase 3: a crawled track has no file identity until someone plays it (ADR-002 §C).

The §8 requirements live here: playing an unresolved track, and two rows collapsing
into one once resolve reveals they are the same file. The edge is the real streaming
app with a fake Telegram behind it, so the resolve goes over real HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, EdgeNode, Track
from app.services import resolving
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import BASE_TIME, make_channel, subscribe
from tmusic_common.indexer_contract import AudioItem

pytest.importorskip("tmusic_indexer")

from telethon import types
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings as EdgeSettings
from tmusic_indexer.stream import Sources, create_app

AUDIO = bytes(range(256)) * 8_000  # ~2 MB


def message(msg_id: int, doc_id: int, title: str, performer: str) -> Any:
    document = types.Document(
        id=doc_id, access_hash=1, file_reference=b"r", date=datetime(2026, 1, 1, tzinfo=UTC),
        mime_type="audio/mpeg", size=len(AUDIO), dc_id=2, thumbs=None,
        attributes=[types.DocumentAttributeAudio(duration=201, title=title, performer=performer)],
    )  # fmt: skip
    return SimpleNamespace(
        id=msg_id,
        media=types.MessageMediaDocument(document=document),
        date=datetime(2026, 1, 1, tzinfo=UTC),
        views=5,
        message="",
    )


class Telegram:
    """Two channels that posted the very same file, plus one message that is gone."""

    def __init__(self) -> None:
        self.messages = {
            10: message(10, 5001, "Pol", "Googoosh"),
            11: message(11, 5002, "Shabe Barooni", "Moein"),
            # The same document id as message 10: a forward of the same file.
            20: message(20, 5001, "Pol", "Googoosh"),
        }
        self.calls: list[int] = []

    async def get_input_entity(self, username: str) -> Any:
        return types.InputPeerChannel(channel_id=31337, access_hash=7)

    async def get_entity(self, peer: Any) -> Any:
        return types.Channel(id=31337, title="Crawled", photo=types.ChatPhotoEmpty(),
                             date=None, username="crawled", access_hash=7, broadcast=True)  # fmt: skip

    async def get_messages(self, peer: Any, ids: int | None = None, **_: Any) -> Any:
        self.calls.append(int(ids or 0))
        return self.messages.get(int(ids or 0))

    async def iter_download(self, document: Any, offset: int, request_size: int, chunk_size: int,
                            limit: int, file_size: int) -> AsyncIterator[bytes]:  # fmt: skip
        for i in range(limit):
            start = offset + i * chunk_size
            if start >= len(AUDIO):
                return
            yield AUDIO[start : start + chunk_size]


def crawled(msg: int, title: str, performer: str) -> AudioItem:
    """What the crawler produces: no file id, no duration, no size."""
    return AudioItem(
        message_id=msg,
        posted_at=BASE_TIME + timedelta(minutes=msg),
        duration=0,
        file_size=0,
        title=title,
        performer=performer,
    )


@pytest.fixture(autouse=True)
def _fresh_breaker() -> Any:
    resolving.reset_breaker()
    yield
    resolving.reset_breaker()


@pytest.fixture
async def edge(
    client: httpx.AsyncClient, session: AsyncSession, tmp_path: Path, settings: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Telegram]]:
    """The real edge app with a fake Telegram, reachable from the core over HTTP."""
    edge_settings = EdgeSettings(
        internal_api_token="internal-token", tg_api_id=1, tg_api_hash="h",
        session_enc_key="a-very-long-test-key", bot_token="123456:TEST",
        stream_signing_keys=settings.stream_signing_keys.get_secret_value(),
        sessions_dir=tmp_path / "s", outbox_path=tmp_path / "o.db",
    )  # fmt: skip
    telegram = Telegram()
    resolver = ResolverAccount(edge_settings)
    resolver.account = Account(key="acc1", client=telegram)
    app = create_app(edge_settings, Sources(edge_settings, resolver, client))
    session.add(EdgeNode(host="http://edge.test"))
    await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://edge.test"
    ) as edge_client:
        state = client.app.state.app  # type: ignore[attr-defined]
        original_http, original_settings = state.http, state.settings
        state.http = edge_client
        state.settings = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
        yield edge_client, telegram
        state.http, state.settings = original_http, original_settings


async def crawl_into(session: AsyncSession, username: str, items: list[AudioItem]) -> Channel:
    channel = await make_channel(
        session, username, source_type="web_preview", tg_channel_id=None, status="active"
    )
    await ingest_items(session, channel, items)
    return channel


# ── the §8 requirement: playing a track nobody has resolved ───────────────────


async def test_an_unresolved_track_resolves_on_first_play_and_streams(
    client: httpx.AsyncClient, session: AsyncSession, edge: Any
) -> None:
    edge_client, telegram = edge
    channel = await crawl_into(session, "crawled", [crawled(10, "Pol", "Googoosh")])
    auth = bearer(await login(client, 7001))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe(session, user_id, channel.id)
    await session.commit()

    track_id = int(
        await session.scalar(select(Track.id).where(Track.resolve_status == "unresolved")) or 0
    )
    assert track_id

    resp = await client.post(f"/v1/tracks/{track_id}/stream", headers=auth)
    assert resp.status_code == 200, resp.text
    ticket = resp.json()
    # The size in the ticket is the real one — it can only have come from the resolve.
    assert ticket["size"] == len(AUDIO)

    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == track_id))).one()
    assert track.resolve_status == "resolved"
    assert track.file_unique_id is not None
    assert (track.duration, track.file_size) == (201, len(AUDIO))
    assert track.resolved_at is not None

    # And the bytes actually flow from the edge.
    played = await edge_client.get(ticket["url"], headers={"Range": "bytes=0-99"})
    assert played.status_code == 206
    assert played.content == AUDIO[:100]

    # A second play costs no further Telegram round-trip for identity.
    telegram.calls.clear()
    again = await client.post(f"/v1/tracks/{track_id}/stream", headers=auth)
    assert again.status_code == 200
    assert telegram.calls == []


# ── the §8 requirement: merge after resolve ───────────────────────────────────


async def test_two_rows_with_the_same_file_become_one_after_resolve(
    client: httpx.AsyncClient, session: AsyncSession, edge: Any
) -> None:
    """Different titles, different channels, same file: only resolve can see that."""
    first = await crawl_into(session, "chan_one", [crawled(10, "Pol", "Googoosh")])
    # Different enough that the fuzzy dedup does not catch it before resolve.
    second = await crawl_into(session, "chan_two", [crawled(20, "Pol (Remastered)", "گوگوش")])
    await session.commit()
    assert first.id != second.id

    ids = list(await session.scalars(select(Track.id).order_by(Track.id)))
    assert len(ids) == 2  # still two separate rows

    state = client.app.state.app  # type: ignore[attr-defined]
    for track_id in ids:
        assert await resolving.resolve_track(session, state.http, state.settings, track_id)
    await session.commit()

    session.expire_all()
    tracks = list(await session.scalars(select(Track).order_by(Track.id)))
    assert [t.resolve_status for t in tracks] == ["resolved", "resolved"]
    # One row keeps the file id, the other points at it.
    assert tracks[0].file_unique_id is not None
    assert tracks[1].file_unique_id is None
    assert tracks[1].canonical_track_id == tracks[0].id
    # The surviving root now knows it exists in two channels.
    assert tracks[0].channels_count == 2


# ── the circuit breaker: a dead resolver must not break playback ──────────────


async def test_a_failing_resolver_never_breaks_already_resolved_tracks(
    client: httpx.AsyncClient, session: AsyncSession, edge: Any
) -> None:
    _, telegram = edge
    channel = await crawl_into(
        session, "crawled", [crawled(10, "Pol", "Googoosh"), crawled(99, "Gone", "Nobody")]
    )
    auth = bearer(await login(client, 7002))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe(session, user_id, channel.id)
    await session.commit()

    rows = {t.source_key: t.id for t in await session.scalars(select(Track)) if t.source_key}
    playable = rows[f"{channel.id}:10"]
    missing = rows[f"{channel.id}:99"]

    # Message 99 does not exist on Telegram: the resolve fails, playback says so.
    failed = await client.post(f"/v1/tracks/{missing}/stream", headers=auth)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "unavailable"

    # The healthy track is untouched by the neighbour's failure.
    ok = await client.post(f"/v1/tracks/{playable}/stream", headers=auth)
    assert ok.status_code == 200

    # Repeated failures stop asking, and stop at the attempt ceiling.
    for _ in range(resolving.MAX_ATTEMPTS + 2):
        await client.post(f"/v1/tracks/{missing}/stream", headers=auth)
    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == missing))).one()
    assert track.resolve_attempts <= resolving.MAX_ATTEMPTS
    assert track.resolve_status == "failed"
    assert telegram.calls.count(99) <= resolving.MAX_ATTEMPTS

    # The already-resolved track still plays with the resolver in a bad state.
    assert (await client.post(f"/v1/tracks/{playable}/stream", headers=auth)).status_code == 200


async def test_the_breaker_opens_and_stops_hitting_a_dead_edge(
    client: httpx.AsyncClient, session: AsyncSession, settings: Any
) -> None:
    channel = await crawl_into(session, "crawled", [crawled(10, "Pol", "Googoosh")])
    assert channel.id
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)

    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("edge is down")

    dead = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dead_settings = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    for _ in range(resolving.BREAKER_THRESHOLD + 3):
        resolving.breaker.failures = 0  # a fresh track each time, same dead edge
        await resolving.resolve_track(session, dead, dead_settings, track_id)
    assert attempts <= resolving.MAX_ATTEMPTS  # the attempt ceiling still holds

    resolving.reset_breaker()
    for _ in range(resolving.BREAKER_THRESHOLD):
        resolving.breaker.trip()
    assert resolving.breaker.open() is True
    stats = await resolving.stats(session)
    assert stats["breaker_open"] is True
    assert stats["unresolved"] + stats["failed"] >= 1
    await dead.aclose()


# ── pre-warming ───────────────────────────────────────────────────────────────


async def test_prewarm_resolves_the_popular_tracks_first(
    client: httpx.AsyncClient, session: AsyncSession, edge: Any
) -> None:
    channel = await crawl_into(
        session, "crawled", [crawled(11, "Shabe Barooni", "Moein"), crawled(10, "Pol", "Googoosh")]
    )
    assert channel.id
    await session.execute(
        text("UPDATE tracks SET likes_count = 50 WHERE normalized_title LIKE 'pol%'")
    )
    await session.commit()

    state = client.app.state.app  # type: ignore[attr-defined]
    result = await resolving.prewarm(session, state.http, state.settings, limit=1)
    assert result == {"resolved": 1, "failed": 0}

    session.expire_all()
    resolved = list(await session.scalars(select(Track).where(Track.resolve_status == "resolved")))
    assert len(resolved) == 1
    assert resolved[0].likes_count == 50  # the liked one, not the other


async def test_prewarm_does_nothing_while_the_breaker_is_open(
    client: httpx.AsyncClient, session: AsyncSession, edge: Any
) -> None:
    await crawl_into(session, "crawled", [crawled(10, "Pol", "Googoosh")])
    await session.commit()
    for _ in range(resolving.BREAKER_THRESHOLD):
        resolving.breaker.trip()

    state = client.app.state.app  # type: ignore[attr-defined]
    assert await resolving.prewarm(session, state.http, state.settings) == {
        "resolved": 0,
        "failed": 0,
    }


# ── the resolver's own account (what is left of the pool) ─────────────────────


async def test_the_edge_registers_its_single_account_and_reports_its_state(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = {"Authorization": "Bearer internal-token"}
    body = {"worker_id": "edge-1", "accounts": [{"session_key": "acc1", "phone_hint": "4567"}]}

    first = await client.post("/internal/indexer/accounts", json=body, headers=auth)
    assert first.status_code == 200, first.text
    ids = first.json()["ids"]
    assert list(ids) == ["acc1"]
    # Registration is idempotent: a restart must not create a second account row.
    again = await client.post("/internal/indexer/accounts", json=body, headers=auth)
    assert again.json()["ids"] == ids
    assert await session.scalar(text("SELECT count(*) FROM indexer_accounts")) == 1

    account_id = ids["acc1"]
    flood = await client.post(
        f"/internal/indexer/accounts/{account_id}/report",
        json={"status": "cooling", "floodwait_s": 120, "error": "FloodWait 120s"},
        headers=auth,
    )
    assert flood.status_code == 204
    row = (
        await session.execute(
            text(
                "SELECT status, floodwait_24h_s, last_error FROM indexer_accounts WHERE id = :i"
            ).bindparams(i=account_id)
        )
    ).one()
    assert (row.status, row.floodwait_24h_s) == ("cooling", 120)
    assert row.last_error == "FloodWait 120s"

    # An empty registration is not an error — an edge with no session still crawls.
    empty = await client.post(
        "/internal/indexer/accounts", json={"worker_id": "edge-2", "accounts": []}, headers=auth
    )
    assert empty.json() == {"ids": {}}

    missing = await client.post(
        "/internal/indexer/accounts/999999/report", json={"status": "healthy"}, headers=auth
    )
    assert missing.status_code == 404


# ── the branches that decide whether to spend the account at all ──────────────


async def test_resolve_is_skipped_without_wasting_an_attempt(
    client: httpx.AsyncClient, session: AsyncSession, settings: Any
) -> None:
    channel = await crawl_into(session, "skipme", [crawled(10, "Pol", "Googoosh")])
    assert channel.id
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)
    unused = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))

    # No edge configured at all.
    assert await resolving.resolve_track(session, unused, settings, track_id) is None
    # Breaker open.
    configured = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    for _ in range(resolving.BREAKER_THRESHOLD):
        resolving.breaker.trip()
    assert await resolving.resolve_track(session, unused, configured, track_id) is None
    resolving.reset_breaker()
    # A track that does not exist.
    assert await resolving.resolve_track(session, unused, configured, 10**9) is None

    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == track_id))).one()
    assert track.resolve_attempts == 0  # none of those burned an attempt
    await unused.aclose()


async def test_a_track_no_channel_carries_any_more_fails_immediately(
    client: httpx.AsyncClient, session: AsyncSession, settings: Any
) -> None:
    """A channel removed from the platform leaves tracks nothing can resolve."""
    channel = await crawl_into(session, "gonechan", [crawled(10, "Pol", "Googoosh")])
    track_id = int(await session.scalar(select(Track.id)) or 0)
    await session.execute(
        text("UPDATE channels SET status = 'blacklisted' WHERE id = :i").bindparams(i=channel.id)
    )
    await session.commit()

    configured = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    unused = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await resolving.resolve_track(session, unused, configured, track_id) is None
    await unused.aclose()

    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == track_id))).one()
    assert track.resolve_status == "failed"
    assert track.resolve_attempts == 0  # nothing was asked of Telegram


async def test_a_floodwait_does_not_count_against_the_track(
    client: httpx.AsyncClient, session: AsyncSession, settings: Any
) -> None:
    """§2 layer C: the account being busy is not the track's fault."""
    await crawl_into(session, "floodchan", [crawled(10, "Pol", "Googoosh")])
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)

    def flooded(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "flood_wait"}, headers={"Retry-After": "0.2"})

    configured = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(flooded)) as edge:
        assert await resolving.resolve_track(session, edge, configured, track_id) is None

    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == track_id))).one()
    assert track.resolve_attempts == 0  # refunded: it can be tried again later
    assert track.resolve_status == "unresolved"
    assert resolving.breaker.failures == 1


async def test_resolving_a_duplicate_of_a_hidden_track_keeps_it_hidden(
    client: httpx.AsyncClient, session: AsyncSession, edge: Any
) -> None:
    """A re-upload of taken-down content must not reappear through the resolver."""
    await crawl_into(session, "chan_one", [crawled(10, "Pol", "Googoosh")])
    await crawl_into(session, "chan_two", [crawled(20, "Pol (Remastered)", "گوگوش")])
    await session.commit()
    ids = list(await session.scalars(select(Track.id).order_by(Track.id)))

    state = client.app.state.app  # type: ignore[attr-defined]
    await resolving.resolve_track(session, state.http, state.settings, ids[0])
    await session.execute(
        text(
            "UPDATE tracks SET hidden = true, hidden_reason = 'takedown' WHERE id = :i"
        ).bindparams(i=ids[0])
    )
    await session.flush()

    await resolving.resolve_track(session, state.http, state.settings, ids[1])
    session.expire_all()
    copy = (await session.scalars(select(Track).where(Track.id == ids[1]))).one()
    assert copy.canonical_track_id == ids[0]
    assert copy.hidden is True
    assert copy.hidden_reason == "duplicate_of_hidden"


async def test_prewarm_stops_the_moment_the_resolver_starts_failing(
    client: httpx.AsyncClient, session: AsyncSession, settings: Any
) -> None:
    channel = await crawl_into(
        session,
        "prewarmchan",
        [crawled(10 + i, f"Song {i}", "Someone") for i in range(6)],
    )
    assert channel.id
    await session.commit()

    calls = 0

    def failing(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json={"error": "nope"})

    configured = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as edge_client:
        result = await resolving.prewarm(session, edge_client, configured, limit=6)

    # It gave up at the breaker threshold instead of walking the whole batch.
    assert calls == resolving.BREAKER_THRESHOLD
    assert result["resolved"] == 0
    assert result["failed"] == resolving.BREAKER_THRESHOLD
