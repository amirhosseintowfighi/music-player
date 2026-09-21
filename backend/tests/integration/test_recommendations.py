"""Phase 7: item-based CF, trending, Discover Weekly, Daily Mix and radio.

The interesting property of a recommender is not that it returns rows — it is that it
returns the *right* rows: co-listened tracks rank above unrelated ones, the user never
gets back what they just played, and one artist cannot take over a mix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Track, User
from app.services import plans, recommendations
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe


async def make_user(session: AsyncSession, tg_id: int) -> User:
    user = User(tg_id=tg_id, first_name=f"U{tg_id}", referral_code=f"r{tg_id}")
    session.add(user)
    await session.flush()
    return user


async def seed_catalog(session: AsyncSession) -> tuple[Channel, list[Track]]:
    """Twelve tracks spread over six artists, all in one channel."""
    channel = await make_channel(session, "mixch")
    artists = ["معین", "گوگوش", "هایده", "داریوش", "محسن یگانه", "شادمهر عقیلی"]
    items = [
        item(f"آهنگ {index}", artists[index % len(artists)], msg=1000 + index, duration=200 + index)
        for index in range(12)
    ]
    await ingest_items(session, channel, items, bot_id=None)
    rows = await session.execute(text("SELECT id FROM tracks ORDER BY id"))
    ids = [row[0] for row in rows]
    tracks = [await session.get(Track, track_id) for track_id in ids]
    return channel, [t for t in tracks if t is not None]


async def play(
    session: AsyncSession, user_id: int, track_id: int, *, completed: bool = True, days_ago: int = 1
) -> None:
    await session.execute(
        text(
            "INSERT INTO play_history (user_id, track_id, played_at, duration_played, completed,"
            " source) VALUES (:u, :t, now() - make_interval(days => :d), 200, :c, 'library')"
        ).bindparams(u=user_id, t=track_id, d=days_ago, c=completed)
    )


async def like(session: AsyncSession, user_id: int, track_id: int) -> None:
    await session.execute(
        text(
            "INSERT INTO likes (user_id, track_id) VALUES (:u, :t) ON CONFLICT DO NOTHING"
        ).bindparams(u=user_id, t=track_id)
    )


# ── similarity ────────────────────────────────────────────────────────────────


async def test_co_listened_tracks_become_neighbours(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    a, b, c = tracks[0].id, tracks[1].id, tracks[7].id
    # Three users listen to a and b together; nobody pairs a with c.
    for tg_id in (301, 302, 303):
        user = await make_user(session, tg_id)
        await play(session, user.id, a)
        await play(session, user.id, b)
    lonely = await make_user(session, 304)
    await play(session, lonely.id, c)

    written = await recommendations.rebuild_similarity(session)
    assert written > 0

    neighbours = await recommendations.similar_ids(session, a)
    assert neighbours[0] == b
    assert c not in neighbours


async def test_a_single_shared_listener_is_not_a_signal(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    user = await make_user(session, 305)
    await play(session, user.id, tracks[0].id)
    await play(session, user.id, tracks[1].id)

    await recommendations.rebuild_similarity(session)
    rows = await session.execute(text("SELECT count(*) FROM track_similarity"))
    assert rows.scalar_one() == 0  # MIN_CO_OCCURRENCE = 2


async def test_skipped_plays_do_not_count(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    for tg_id in (306, 307):
        user = await make_user(session, tg_id)
        await play(session, user.id, tracks[0].id, completed=False)
        await play(session, user.id, tracks[1].id, completed=False)
    await recommendations.rebuild_similarity(session)
    rows = await session.execute(text("SELECT count(*) FROM track_similarity"))
    assert rows.scalar_one() == 0


async def test_likes_also_build_the_matrix(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    for tg_id in (308, 309):
        user = await make_user(session, tg_id)
        await like(session, user.id, tracks[2].id)
        await like(session, user.id, tracks[3].id)
    await recommendations.rebuild_similarity(session)
    assert tracks[3].id in await recommendations.similar_ids(session, tracks[2].id)


async def test_rebuild_is_a_full_replacement(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    for tg_id in (310, 311):
        user = await make_user(session, tg_id)
        await play(session, user.id, tracks[0].id)
        await play(session, user.id, tracks[1].id)
    await recommendations.rebuild_similarity(session)
    first = await recommendations.similar_ids(session, tracks[0].id)
    assert first

    await session.execute(text("DELETE FROM play_history"))
    await session.execute(text("DELETE FROM likes"))
    await recommendations.rebuild_similarity(session)
    rows = await session.execute(text("SELECT count(*) FROM track_similarity"))
    assert rows.scalar_one() == 0  # stale neighbours are never left behind


async def test_similar_falls_back_to_the_same_artist(session: AsyncSession) -> None:
    """A brand-new track has no neighbours; the answer must still be useful."""
    _, tracks = await seed_catalog(session)
    same_artist = [t.id for t in tracks if t.normalized_artist == tracks[0].normalized_artist]
    found = await recommendations.similar_ids(session, tracks[0].id)
    assert found
    assert set(found) <= set(same_artist) - {tracks[0].id}


# ── trending ──────────────────────────────────────────────────────────────────


async def test_trending_ranks_by_distinct_listeners(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    popular, quiet = tracks[0].id, tracks[1].id
    for tg_id in (320, 321, 322):
        user = await make_user(session, tg_id)
        await play(session, user.id, popular)
    one = await make_user(session, 323)
    # One user playing the same track ten times must not outrank three listeners.
    for _ in range(10):
        await play(session, one.id, quiet)

    await recommendations.rebuild_trending(session)
    ranked = await recommendations.trending_ids(session, "7d", "plays", 10)
    assert ranked[0] == popular
    assert ranked.index(popular) < ranked.index(quiet)


async def test_trending_windows_are_independent(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    recent, old = tracks[0].id, tracks[1].id
    for tg_id in (330, 331):
        user = await make_user(session, tg_id)
        await play(session, user.id, recent, days_ago=0)
        await play(session, user.id, old, days_ago=20)

    await recommendations.rebuild_trending(session)
    day = await recommendations.trending_ids(session, "24h", "plays", 10)
    month = await recommendations.trending_ids(session, "30d", "plays", 10)
    assert recent in day
    assert old not in day
    assert {recent, old} <= set(month)


async def test_most_added_uses_channel_count(session: AsyncSession) -> None:
    channel_a = await make_channel(session, "cha")
    channel_b = await make_channel(session, "chb")
    shared = item("آهنگ مشترک", "معین", msg=1, fuid="AgADshared01")
    await ingest_items(session, channel_a, [shared, item("تک", "گوگوش", msg=2)], bot_id=None)
    await ingest_items(session, channel_b, [shared], bot_id=None)

    await recommendations.rebuild_trending(session)
    ranked = await recommendations.trending_ids(session, "7d", "most_added", 10)
    row = await session.execute(text("SELECT id FROM tracks WHERE file_unique_id = 'AgADshared01'"))
    assert ranked == [row.scalar_one()]  # the single-channel track is not "most added"


async def test_rising_prefers_growth_over_volume(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    steady, rising = tracks[0].id, tracks[1].id
    for tg_id in (340, 341, 342):
        user = await make_user(session, tg_id)
        await play(session, user.id, steady, days_ago=0)
        await play(session, user.id, steady, days_ago=10)
        await play(session, user.id, steady, days_ago=11)
        await play(session, user.id, rising, days_ago=0)

    await recommendations.rebuild_trending(session)
    ranked = await recommendations.trending_ids(session, "7d", "rising", 10)
    assert ranked.index(rising) < ranked.index(steady)


async def test_trending_falls_back_before_the_first_run(session: AsyncSession) -> None:
    await seed_catalog(session)
    ranked = await recommendations.trending_ids(session, "7d", "plays", 5)
    assert len(ranked) == 5  # never an empty Home screen


# ── generated playlists ───────────────────────────────────────────────────────


async def test_discover_weekly_excludes_what_the_user_heard(session: AsyncSession) -> None:
    channel, tracks = await seed_catalog(session)
    me = await make_user(session, 350)
    await subscribe(session, me.id, channel.id)
    heard, target = tracks[0].id, tracks[1].id
    await play(session, me.id, heard)
    # Two other users pair the two tracks, so the matrix links them.
    for tg_id in (351, 352):
        other = await make_user(session, tg_id)
        await play(session, other.id, heard)
        await play(session, other.id, target)
    await recommendations.rebuild_similarity(session)

    playlist = await recommendations.discover_weekly(session, me.id)
    ids = await session.execute(
        text(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = :p ORDER BY position"
        ).bindparams(p=playlist.id)
    )
    chosen = [row[0] for row in ids]
    assert target in chosen
    assert heard not in chosen  # already played in the last 30 days
    assert playlist.kind == "discover_weekly"
    assert playlist.generated_for == recommendations.week_start()


async def test_discover_weekly_is_idempotent_within_the_week(session: AsyncSession) -> None:
    channel, _ = await seed_catalog(session)
    me = await make_user(session, 353)
    await subscribe(session, me.id, channel.id)

    first = await recommendations.discover_weekly(session, me.id)
    second = await recommendations.discover_weekly(session, me.id)
    assert first.id == second.id
    rows = await session.execute(
        text(
            "SELECT count(*) FROM playlists WHERE user_id = :u AND kind = 'discover_weekly'"
        ).bindparams(u=me.id)
    )
    assert rows.scalar_one() == 1
    assert second.tracks_count == first.tracks_count


async def test_discover_weekly_cold_start_uses_the_library(session: AsyncSession) -> None:
    channel, _ = await seed_catalog(session)
    me = await make_user(session, 354)
    await subscribe(session, me.id, channel.id)

    playlist = await recommendations.discover_weekly(session, me.id)
    assert playlist.tracks_count > 0  # no history at all, still a usable mix


async def test_a_mix_never_becomes_one_artist(session: AsyncSession) -> None:
    channel = await make_channel(session, "onech")
    await ingest_items(
        session,
        channel,
        [item(f"ترانه {i}", "معین", msg=2000 + i, duration=180 + i) for i in range(10)],
        bot_id=None,
    )
    me = await make_user(session, 355)
    await subscribe(session, me.id, channel.id)

    playlist = await recommendations.discover_weekly(session, me.id)
    rows = await session.execute(
        text(
            "SELECT count(*) FROM playlist_tracks pt"
            " JOIN track_artists ta ON ta.track_id = pt.track_id AND ta.role = 'primary'"
            " WHERE pt.playlist_id = :p"
        ).bindparams(p=playlist.id)
    )
    assert rows.scalar_one() <= recommendations.MAX_PER_ARTIST


async def test_daily_mixes_follow_the_artists_the_user_plays(session: AsyncSession) -> None:
    channel, _ = await seed_catalog(session)
    # A mix needs a real back catalogue, so give this artist eight tracks.
    await ingest_items(
        session,
        channel,
        [item(f"معین {i}", "معین", msg=3000 + i, duration=190 + i) for i in range(8)],
        bot_id=None,
    )
    me = await make_user(session, 356)
    await subscribe(session, me.id, channel.id)
    rows = await session.execute(
        text(
            "SELECT t.id FROM tracks t JOIN track_artists ta ON ta.track_id = t.id"
            " JOIN artists a ON a.id = ta.artist_id WHERE a.normalized_name = 'معین'"
        )
    )
    for row in rows:
        await play(session, me.id, row[0])

    mixes = await recommendations.daily_mixes(session, me.id)
    assert mixes
    assert mixes[0].kind == "daily_mix"
    assert mixes[0].generated_for == datetime.now(UTC).date()


async def test_generated_playlists_are_pruned(session: AsyncSession) -> None:
    channel, _ = await seed_catalog(session)
    me = await make_user(session, 357)
    await subscribe(session, me.id, channel.id)
    old_week = recommendations.week_start() - timedelta(days=30)
    await recommendations.discover_weekly(session, me.id, for_week=old_week)

    removed = await recommendations.prune_generated(session)
    assert removed == 1
    rows = await session.execute(
        text("SELECT count(*) FROM playlists WHERE kind = 'discover_weekly'")
    )
    assert rows.scalar_one() == 0


# ── radio ─────────────────────────────────────────────────────────────────────


async def test_radio_starts_from_the_seed(session: AsyncSession) -> None:
    _, tracks = await seed_catalog(session)
    seed = tracks[0].id
    for tg_id in (360, 361):
        user = await make_user(session, tg_id)
        await play(session, user.id, seed)
        await play(session, user.id, tracks[1].id)
    await recommendations.rebuild_similarity(session)

    station = await recommendations.radio_ids(session, seed, limit=10)
    assert station[0] == seed
    assert tracks[1].id in station
    assert len(station) == len(set(station))  # no repeats


# ── API ───────────────────────────────────────────────────────────────────────


async def test_discover_endpoint_returns_sections(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel, _tracks = await seed_catalog(session)
    await recommendations.rebuild_trending(session)
    await session.commit()

    token = await login(client, 8201)
    user_id = token["me"]["id"]
    await subscribe(session, user_id, channel.id)
    await session.commit()

    resp = await client.get("/v1/discover", headers=bearer(token))
    assert resp.status_code == 200, resp.text
    ids = {section["id"] for section in resp.json()["sections"]}
    assert "most-added" in ids or "trending-7d" in ids
    plans.clear_caches()


async def test_discover_refresh_creates_the_weekly_mix(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel, _ = await seed_catalog(session)
    await session.commit()
    token = await login(client, 8202)
    await subscribe(session, token["me"]["id"], channel.id)
    await session.commit()

    resp = await client.post("/v1/discover/refresh", headers=bearer(token))
    assert resp.status_code == 200
    titles = [section["title"] for section in resp.json()["sections"]]
    assert "کشف هفتگی" in titles
    plans.clear_caches()


async def test_daily_mixes_are_a_pro_feature(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel, _ = await seed_catalog(session)
    await ingest_items(
        session,
        channel,
        [item(f"معین {i}", "معین", msg=4000 + i, duration=195 + i) for i in range(8)],
        bot_id=None,
    )
    await session.commit()
    token = await login(client, 8203)
    user_id = token["me"]["id"]
    await subscribe(session, user_id, channel.id)
    rows = await session.execute(
        text(
            "SELECT t.id FROM tracks t JOIN track_artists ta ON ta.track_id = t.id"
            " JOIN artists a ON a.id = ta.artist_id WHERE a.normalized_name = 'معین'"
        )
    )
    for row in rows:
        await play(session, user_id, row[0])
    await session.commit()

    await recommendations.refresh_for_user(session, user_id)
    await session.commit()

    free = await client.get("/v1/discover", headers=bearer(token))
    assert not [s for s in free.json()["sections"] if s["title"].startswith("Daily Mix")]

    await session.execute(
        text(
            "UPDATE users SET plan_code = 'pro_monthly',"
            " premium_until = now() + interval '30 days' WHERE id = :u"
        ).bindparams(u=user_id)
    )
    await session.commit()
    plans.clear_caches()
    pro = await client.get("/v1/discover", headers=bearer(token))
    assert [s for s in pro.json()["sections"] if s["title"].startswith("Daily Mix")]
    plans.clear_caches()


async def test_trending_endpoint_validates_its_window(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await seed_catalog(session)
    await session.commit()
    token = await login(client, 8204)
    ok = await client.get("/v1/trending?window=24h&kind=rising", headers=bearer(token))
    assert ok.status_code == 200
    bad = await client.get("/v1/trending?window=1y", headers=bearer(token))
    assert bad.status_code == 422


async def test_similar_and_radio_endpoints(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, tracks = await seed_catalog(session)
    await session.commit()
    token = await login(client, 8205)

    similar = await client.get(f"/v1/tracks/{tracks[0].id}/similar", headers=bearer(token))
    assert similar.status_code == 200
    radio = await client.get(f"/v1/tracks/{tracks[0].id}/radio?limit=5", headers=bearer(token))
    assert radio.status_code == 200
    assert radio.json()["items"][0]["id"] == tracks[0].id


async def test_discover_does_not_scale_queries_with_sections(
    client: httpx.AsyncClient, session: AsyncSession, count_queries: Any
) -> None:
    """Hydration is batched: more sections must not mean more queries per track."""
    channel, _tracks = await seed_catalog(session)
    await recommendations.rebuild_trending(session)
    await session.commit()
    token = await login(client, 8206)
    await subscribe(session, token["me"]["id"], channel.id)
    await session.commit()

    with count_queries() as counter:
        resp = await client.get("/v1/discover", headers=bearer(token))
    assert resp.status_code == 200
    assert counter.count < 25, counter.statements
    plans.clear_caches()


async def test_nightly_job_runs_end_to_end(session: AsyncSession, engine: Any) -> None:
    from app.db import make_sessionmaker
    from app.workers import jobs

    channel, tracks = await seed_catalog(session)
    me = await make_user(session, 370)
    await subscribe(session, me.id, channel.id)
    for tg_id in (371, 372):
        other = await make_user(session, tg_id)
        await play(session, other.id, tracks[0].id)
        await play(session, other.id, tracks[1].id)
    await session.commit()

    ctx = {"sessionmaker": make_sessionmaker(engine)}
    stats = await jobs.recommendations_nightly(ctx)
    assert stats["similar"] > 0
    assert stats["trending"] > 0

    mixes = await jobs.generate_mixes(ctx)
    assert mixes["users"] >= 1
    assert mixes["failed"] == 0

    session.expire_all()
    rows = await session.execute(
        text("SELECT count(*) FROM playlists WHERE kind IN ('discover_weekly','daily_mix')")
    )
    assert rows.scalar_one() >= 1
    plans.clear_caches()
