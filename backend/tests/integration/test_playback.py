"""Phase 10: the playback contract — prefetch tickets, telemetry, failure behaviour.

Three properties, all of which are about what happens *around* a successful play:
warming the next track must be free, a resolver outage must be legible instead of a
frozen player, and the four numbers that tell us playback is healthy must move.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.models import Track
from app.services import plans, resolving
from app.services.ingest import ingest_items
from app.services.stream import PREFETCH_BYTES
from tests.conftest import bearer, login
from tests.integration.helpers import BASE_TIME, item, make_channel, subscribe
from tests.integration.test_hardening import counter_value
from tmusic_common.indexer_contract import AudioItem
from tmusic_common.stream_ticket import verify

pytest.importorskip("tmusic_indexer")


async def playable_track(
    session: AsyncSession, client: httpx.AsyncClient, tg_id: int
) -> tuple[int, dict[str, str]]:
    """A resolved track in the user's library, plus that user's auth header."""
    channel = await make_channel(session, f"pbch{tg_id}")
    await ingest_items(session, channel, [item("Pol", "Googoosh", msg=5000 + tg_id)], bot_id=None)
    auth = bearer(await login(client, tg_id))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe(session, user_id, channel.id)
    session.add_all([])
    await session.execute(
        text("INSERT INTO edge_nodes (host) VALUES ('http://edge.test') ON CONFLICT DO NOTHING")
    )
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)
    return track_id, auth


# ── prefetch tickets ──────────────────────────────────────────────────────────


async def test_a_prefetch_ticket_does_not_spend_the_daily_limit(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Warming the next track must not cost the user a play they never heard."""
    track_id, auth = await playable_track(session, client, 9101)
    await session.execute(
        text(
            "UPDATE plans SET limits = jsonb_set(limits, '{daily_plays}', '1') WHERE code = 'free'"
        )
    )
    await session.commit()
    plans.clear_caches()

    # Any number of prefetches is free …
    for _ in range(3):
        warm = await client.post(f"/v1/tracks/{track_id}/stream?prefetch=1", headers=auth)
        assert warm.status_code == 200, warm.text

    # … and the one real play still fits inside a limit of one.
    played = await client.post(f"/v1/tracks/{track_id}/stream", headers=auth)
    assert played.status_code == 200

    await session.execute(
        text(
            "UPDATE plans SET limits = jsonb_set(limits, '{daily_plays}', '20') WHERE code = 'free'"
        )
    )
    await session.commit()
    plans.clear_caches()


async def test_a_prefetch_ticket_carries_a_byte_ceiling(
    client: httpx.AsyncClient, session: AsyncSession, settings: Any
) -> None:
    track_id, auth = await playable_track(session, client, 9102)

    warm = (await client.post(f"/v1/tracks/{track_id}/stream?prefetch=1", headers=auth)).json()
    full = (await client.post(f"/v1/tracks/{track_id}/stream", headers=auth)).json()

    keys = settings.signing_keys
    warm_ticket = verify(warm["url"].split("t=")[1], keys)
    full_ticket = verify(full["url"].split("t=")[1], keys)
    assert warm_ticket.max_bytes == PREFETCH_BYTES
    assert full_ticket.max_bytes == 0  # the whole file
    assert warm_ticket.user_id == full_ticket.user_id  # still bound to the user


async def test_the_edge_refuses_to_serve_past_a_prefetch_ceiling(settings: Any) -> None:
    """The ceiling is inside the signature, so the client cannot ask for more."""
    from tmusic_indexer.account import Account, ResolverAccount
    from tmusic_indexer.config import Settings as EdgeSettings
    from tmusic_indexer.stream import Sources, create_app

    from tests.integration.test_lazy_resolve import AUDIO, Telegram
    from tmusic_common.stream_ticket import StreamTicket, sign

    edge_settings = EdgeSettings(
        internal_api_token="internal-token", tg_api_id=1, tg_api_hash="h",
        session_enc_key="a-very-long-test-key", bot_token="123456:TEST",
        stream_signing_keys=settings.stream_signing_keys.get_secret_value(),
    )  # fmt: skip
    resolver = ResolverAccount(edge_settings)
    resolver.account = Account(key="acc1", client=Telegram())
    async with httpx.AsyncClient() as http:
        app = create_app(edge_settings, Sources(edge_settings, resolver, http))
        ticket = StreamTicket(
            track_id=1, user_id=1, exp=2**31, size=len(AUDIO), mime="audio/mpeg",
            channel_id=31337, channel_username="crawled", message_id=10,
            max_bytes=1024,
        )  # fmt: skip
        token = sign(ticket, edge_settings.signing_keys[0])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://edge.test"
        ) as edge:
            url = f"/s/1?t={token}"
            # No Range: it gets the head of the file, not the file.
            whole = await edge.get(url)
            assert whole.status_code == 200
            assert len(whole.content) == 1024

            # A Range inside the ceiling works …
            inside = await edge.get(url, headers={"Range": "bytes=0-99"})
            assert inside.status_code == 206
            assert inside.content == AUDIO[:100]

            # … one past it is clamped, never served beyond the ceiling …
            clamped = await edge.get(url, headers={"Range": "bytes=512-4096"})
            assert clamped.status_code == 206
            assert clamped.content == AUDIO[512:1024]

            # … and one entirely beyond it is refused.
            beyond = await edge.get(url, headers={"Range": "bytes=2048-4096"})
            assert beyond.status_code == 416


# ── failure behaviour: legible, never a frozen player ─────────────────────────


async def test_a_resolver_outage_answers_clearly_and_records_the_demand(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "outagech", source_type="web_preview", status="active")
    await ingest_items(
        session,
        channel,
        [
            AudioItem(
                message_id=77,
                posted_at=BASE_TIME + timedelta(minutes=1),
                duration=0,
                file_size=0,
                title="Someone - Unresolved",
            )
        ],
    )
    auth = bearer(await login(client, 9103))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe(session, user_id, channel.id)
    await session.execute(
        text("INSERT INTO edge_nodes (host) VALUES ('http://edge.test') ON CONFLICT DO NOTHING")
    )
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)

    resolving.reset_breaker()
    before = counter_value(metrics.PLAYBACK_INLINE_RESOLVE, result="failed")
    resp = await client.post(f"/v1/tracks/{track_id}/stream", headers=auth)

    # A named reason, not a generic 500 and not a hanging request.
    assert resp.status_code == 503
    assert resp.json()["error"]["details"]["reason"] == "resolving"
    assert counter_value(metrics.PLAYBACK_INLINE_RESOLVE, result="failed") == before + 1

    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == track_id))).one()
    assert track.resolve_requests == 1  # the demand survived the failed request

    # Asking again raises the demand; pre-warm will see it first.
    await client.post(f"/v1/tracks/{track_id}/stream", headers=auth)
    session.expire_all()
    track = (await session.scalars(select(Track).where(Track.id == track_id))).one()
    assert track.resolve_requests == 2


async def test_prewarm_goes_after_what_people_actually_asked_for(
    session: AsyncSession,
) -> None:
    channel = await make_channel(session, "warmch", source_type="web_preview", status="active")
    await ingest_items(
        session,
        channel,
        [
            AudioItem(
                message_id=i,
                posted_at=BASE_TIME + timedelta(minutes=i),
                duration=0,
                file_size=0,
                title=f"Artist - Song {i}",
            )
            for i in (1, 2, 3)
        ],
    )
    ids = list(await session.scalars(select(Track.id).order_by(Track.id)))
    # The middle one is liked; the last one somebody tried to play and could not.
    await session.execute(
        text("UPDATE tracks SET likes_count = 99 WHERE id = :i").bindparams(i=ids[1])
    )
    await resolving.record_demand(session, ids[2])

    queued = list(
        await session.scalars(
            resolving._PREWARM_SQL.bindparams(limit=3, max_attempts=resolving.MAX_ATTEMPTS)
        )
    )
    assert queued[0] == ids[2]  # demand beats popularity
    assert queued[1] == ids[1]  # popularity beats nothing


async def test_featured_channels_and_playlists_are_warmed_before_the_rest(
    session: AsyncSession,
) -> None:
    plain = await make_channel(session, "plainch", source_type="web_preview", status="active")
    featured = await make_channel(
        session, "featuredch", source_type="web_preview", status="active", is_featured=True
    )
    for channel, msg in ((plain, 11), (featured, 12)):
        await ingest_items(
            session,
            channel,
            [
                AudioItem(
                    message_id=msg,
                    posted_at=BASE_TIME + timedelta(minutes=msg),
                    duration=0,
                    file_size=0,
                    title=f"Artist - Song {msg}",
                )
            ],
        )
    await session.flush()
    ids = {
        int(row.message_id): int(row.track_id)
        for row in (
            await session.execute(text("SELECT message_id, track_id FROM channel_tracks"))
        ).all()
    }

    queued = list(
        await session.scalars(
            resolving._PREWARM_SQL.bindparams(limit=5, max_attempts=resolving.MAX_ATTEMPTS)
        )
    )
    assert queued.index(ids[12]) < queued.index(ids[11])  # featured first


# ── telemetry: the two numbers only the client can see ────────────────────────


async def test_the_client_can_report_what_only_it_can_measure(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 9104))
    underruns = counter_value(metrics.PLAYBACK_UNDERRUNS)
    errors = counter_value(metrics.PLAYBACK_ERRORS, kind="network")

    assert (
        await client.post("/v1/telemetry/playback", json={"kind": "start", "ms": 850}, headers=auth)
    ).status_code == 204
    assert (
        await client.post("/v1/telemetry/playback", json={"kind": "underrun"}, headers=auth)
    ).status_code == 204
    assert (
        await client.post(
            "/v1/telemetry/playback",
            json={"kind": "error", "reason": "network"},
            headers=auth,
        )
    ).status_code == 204

    assert counter_value(metrics.PLAYBACK_UNDERRUNS) == underruns + 1
    assert counter_value(metrics.PLAYBACK_ERRORS, kind="network") == errors + 1
    # The start histogram has the sample, in seconds.
    assert metrics.PLAYBACK_START._sum.get() >= 0.85


async def test_telemetry_needs_a_user_and_rejects_nonsense(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    assert (await client.post("/v1/telemetry/playback", json={"kind": "start"})).status_code == 401
    auth = bearer(await login(client, 9105))
    bad = await client.post("/v1/telemetry/playback", json={"kind": "explode"}, headers=auth)
    assert bad.status_code == 422


# ── channel attribution (ADR-003 §2-5) ────────────────────────────────────────


async def test_a_track_is_credited_to_the_channel_worth_joining(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The chip exists to win the channel a member, so it shows one they lack."""
    from tests.integration.helpers import subscribe as subscribe_to

    joined = await make_channel(session, "already_mine", status="active", subscribers_count=9_000)
    big = await make_channel(session, "big_one", status="active", subscribers_count=5_000)
    featured = await make_channel(
        session, "the_shelf", status="active", is_featured=True, subscribers_count=100
    )
    song = item("Pol", "Googoosh", msg=6001)
    for channel in (joined, big, featured):
        await ingest_items(session, channel, [song], bot_id=None)

    auth = bearer(await login(client, 9301))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe_to(session, user_id, joined.id, big.id, featured.id)
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)

    # Subscribed to all three: the chip falls back to featured-then-biggest.
    everything = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert everything["channel"]["username"] == "the_shelf"
    assert everything["channel"]["joined"] is True

    # Leaving the featured one makes it the obvious thing to show …
    await session.execute(
        text("DELETE FROM user_channels WHERE user_id = :u AND channel_id = :c").bindparams(
            u=user_id, c=featured.id
        )
    )
    await session.commit()
    again = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert again["channel"]["username"] == "the_shelf"
    assert again["channel"]["joined"] is False

    # … and with two unjoined channels, featured still beats the bigger one.
    await session.execute(
        text("DELETE FROM user_channels WHERE user_id = :u AND channel_id = :c").bindparams(
            u=user_id, c=big.id
        )
    )
    await session.commit()
    both = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert both["channel"]["username"] == "the_shelf"


async def test_attribution_prefers_an_unjoined_channel_over_a_featured_one(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    from tests.integration.helpers import subscribe as subscribe_to

    featured = await make_channel(
        session, "mine_featured", status="active", is_featured=True, subscribers_count=50_000
    )
    stranger = await make_channel(session, "unknown_chan", status="active", subscribers_count=10)
    song = item("Gole Sangam", "Hayedeh", msg=6002)
    for channel in (featured, stranger):
        await ingest_items(session, channel, [song], bot_id=None)

    auth = bearer(await login(client, 9302))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe_to(session, user_id, featured.id)
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)

    out = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert out["channel"]["username"] == "unknown_chan"  # not the one they already have
    assert out["channel"]["joined"] is False
    assert out["channels_count"] == 2


async def test_attribution_is_present_everywhere_tracks_are(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "everywhere_ch", status="active")
    await ingest_items(session, channel, [item("Pol", "Googoosh", msg=6003)], bot_id=None)
    auth = bearer(await login(client, 9303))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe(session, user_id, channel.id)
    await session.commit()

    listed = (await client.get("/v1/library/tracks", headers=auth)).json()["items"]
    assert listed[0]["channel"]["username"] == "everywhere_ch"
    assert listed[0]["channel"]["title"] == "Everywhere_Ch"


# ── user reports (feeds both the takedown queue and phase 13's metadata queue) ─


async def test_a_listener_can_report_a_track(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "reportch", status="active")
    await ingest_items(session, channel, [item("Pol", "Googoosh", msg=6100)], bot_id=None)
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)
    auth = bearer(await login(client, 9401))

    first = await client.post(
        f"/v1/tracks/{track_id}/report",
        json={"reason": "wrong_metadata", "details": "خواننده اشتباه است"},
        headers=auth,
    )
    assert first.status_code == 201, first.text
    report_id = first.json()["id"]
    assert first.json()["status"] == "open"

    # Reporting again is the same complaint, not a second one.
    again = await client.post(
        f"/v1/tracks/{track_id}/report",
        json={"reason": "wrong_metadata", "details": "هنوز غلط است"},
        headers=auth,
    )
    assert again.json()["id"] == report_id
    assert await session.scalar(text("SELECT count(*) FROM reports")) == 1
    row = (
        await session.execute(
            text(
                "SELECT entity_type, reason, details, due_at FROM reports WHERE id = :i"
            ).bindparams(i=report_id)
        )
    ).one()
    assert (row.entity_type, row.reason) == ("track", "wrong_metadata")
    assert row.details == "هنوز غلط است"

    # A copyright report is on the 48-hour clock instead of the ordinary queue.
    other = await client.post(
        f"/v1/tracks/{track_id}/report",
        json={"reason": "copyright"},
        headers=bearer(await login(client, 9402)),
    )
    assert other.status_code == 201
    due = await session.scalar(
        text("SELECT due_at FROM reports WHERE id = :i").bindparams(i=other.json()["id"])
    )
    assert due < datetime.now(UTC) + timedelta(hours=49)


async def test_reports_are_rejected_for_unknown_tracks_and_rubbish_reasons(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 9403))
    assert (
        await client.post("/v1/tracks/999999/report", json={"reason": "broken"}, headers=auth)
    ).status_code == 404
    assert (
        await client.post("/v1/tracks/1/report", json={"reason": "because"}, headers=auth)
    ).status_code == 422


# ── cover palette (ADR-003 phase 12) ──────────────────────────────────────────


async def test_the_first_client_to_render_a_cover_teaches_everyone_else(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "palettech", status="active")
    await ingest_items(session, channel, [item("Pol", "Googoosh", msg=6200)], bot_id=None)
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)
    auth = bearer(await login(client, 9501))

    fresh = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert fresh["palette"] is None

    saved = await client.put(
        f"/v1/tracks/{track_id}/palette",
        json={"colors": ["#2bd9c4", "#4a6bff", "#b02bd9"]},
        headers=auth,
    )
    assert saved.status_code == 204

    seen = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert seen["palette"] == "#2bd9c4,#4a6bff,#b02bd9"

    # A second client does not get to repaint it.
    await client.put(
        f"/v1/tracks/{track_id}/palette",
        json={"colors": ["#000000", "#000000", "#000000"]},
        headers=bearer(await login(client, 9502)),
    )
    again = (await client.get(f"/v1/tracks/{track_id}", headers=auth)).json()
    assert again["palette"] == "#2bd9c4,#4a6bff,#b02bd9"


async def test_a_palette_has_to_be_three_colours(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 9503))
    for body in (
        {"colors": ["#fff", "#fff", "#fff"]},
        {"colors": ["#2bd9c4", "#4a6bff"]},
        {"colors": ["javascript:alert(1)", "#4a6bff", "#b02bd9"]},
    ):
        resp = await client.put("/v1/tracks/1/palette", json=body, headers=auth)
        assert resp.status_code == 422, body
