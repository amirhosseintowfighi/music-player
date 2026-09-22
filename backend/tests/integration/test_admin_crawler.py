"""Phase 5, back end: what the crawler, parser and resolver pages are made of.

The panel is only a view; everything it shows and every button it has is one of
these endpoints, so this is where the behaviour is pinned down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel
from app.services import resolving
from tests.integration.helpers import make_channel
from tests.integration.test_admin import admin_token, make_admin


async def owner(
    client: httpx.AsyncClient, session: AsyncSession, tg_id: int = 78001
) -> dict[str, str]:
    await make_admin(session, tg_id)
    await session.commit()
    return await admin_token(client, tg_id)


async def crawl_channel(
    session: AsyncSession, username: str, **values: str | int | None
) -> Channel:
    values.setdefault("source_type", "web_preview")
    return await make_channel(session, username, **values)


# ── crawler health ────────────────────────────────────────────────────────────


async def test_the_crawler_page_shows_every_channels_state(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await crawl_channel(session, "healthy", status="active", crawl_status="idle", progress_pct=100)
    await crawl_channel(
        session,
        "broken",
        status="indexing",
        crawl_status="error",
        crawl_error="502 from telegram",
        fail_count=3,
        progress_pct=40,
    )
    await crawl_channel(session, "nopreview", crawl_status="preview_disabled")
    await session.commit()
    headers = await owner(client, session)

    health = (await client.get("/admin/crawler", headers=headers)).json()
    assert health["channels"] >= 3
    assert health["errored"] == 1
    assert health["preview_disabled"] == 1
    assert "unresolved_tracks" in health

    rows = (await client.get("/admin/crawler/channels", headers=headers)).json()
    by_name = {row["username"]: row for row in rows}
    # The broken one is first: an admin opening this page is looking for trouble.
    assert rows[0]["username"] == "broken"
    assert by_name["broken"]["crawl_error"] == "502 from telegram"
    assert by_name["broken"]["fail_count"] == 3
    assert by_name["healthy"]["progress_pct"] == 100

    only_errors = (await client.get("/admin/crawler/channels?status=error", headers=headers)).json()
    assert [row["username"] for row in only_errors] == ["broken"]


async def test_recrawl_buttons_requeue_a_channel(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await crawl_channel(
        session,
        "stuck",
        status="active",
        crawl_status="error",
        crawl_error="boom",
        fail_count=4,
        progress_pct=100,
        oldest_crawled_msg_id=1,
        newest_crawled_msg_id=500,
    )
    cid = channel.id
    await session.execute(
        text(
            "UPDATE channels SET next_crawl_at = now() + interval '1 day' WHERE id = :i"
        ).bindparams(i=cid)
    )
    await session.commit()
    headers = await owner(client, session)

    # Incremental: keep the cursors, just look again now.
    resp = await client.post(f"/admin/crawler/channels/{cid}/recrawl", headers=headers)
    assert resp.status_code == 200
    session.expire_all()
    channel = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert (channel.crawl_status, channel.fail_count, channel.crawl_error) == ("idle", 0, None)
    assert channel.next_crawl_at <= datetime.now(UTC) + timedelta(seconds=5)
    assert channel.oldest_crawled_msg_id == 1  # nothing thrown away

    # Full: walk the channel from the top again.
    await client.post(f"/admin/crawler/channels/{cid}/recrawl?full=true", headers=headers)
    session.expire_all()
    channel = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert channel.oldest_crawled_msg_id is None
    assert channel.newest_crawled_msg_id is None
    assert (channel.progress_pct, channel.status) == (0, "indexing")

    audited = await session.scalar(
        text("SELECT count(*) FROM audit_log WHERE action = 'channel.recrawl'")
    )
    assert audited == 2


# ── parser monitor ────────────────────────────────────────────────────────────


async def add_day(session: AsyncSession, days_ago: int, messages: int, audio: int) -> None:
    await session.execute(
        text(
            "INSERT INTO crawl_stats_daily (day, pages, messages, audio_items, empty_pages)"
            " VALUES (current_date - CAST(:d AS int), 10, :m, :a, 0)"
            " ON CONFLICT (day) DO UPDATE SET messages = excluded.messages,"
            " audio_items = excluded.audio_items"
        ).bindparams(d=days_ago, m=messages, a=audio)
    )


async def test_the_parser_monitor_alerts_when_extraction_collapses(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The failure mode this exists for: Telegram changes its markup overnight."""
    for day in range(1, 6):
        await add_day(session, day, messages=200, audio=180)  # a healthy 0.9
    await add_day(session, 0, messages=200, audio=4)  # today: something broke
    await session.commit()
    headers = await owner(client, session)

    report = (await client.get("/admin/crawler/parser", headers=headers)).json()
    assert report["alert"] is True
    assert report["today_rate"] < 0.1
    assert report["expected_rate"] > 0.8
    assert len(report["days"]) == 6
    assert report["days"][0]["messages"] == 200


async def test_a_normal_day_does_not_alert(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    for day in range(1, 6):
        await add_day(session, day, messages=200, audio=180)
    await add_day(session, 0, messages=200, audio=170)
    await session.commit()
    headers = await owner(client, session)

    report = (await client.get("/admin/crawler/parser", headers=headers)).json()
    assert report["alert"] is False


async def test_a_quiet_day_is_not_mistaken_for_a_broken_parser(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Two crawled messages is not evidence of anything; it must not page anyone."""
    for day in range(1, 6):
        await add_day(session, day, messages=200, audio=180)
    await add_day(session, 0, messages=2, audio=0)
    await session.commit()
    headers = await owner(client, session)

    assert (await client.get("/admin/crawler/parser", headers=headers)).json()["alert"] is False


async def test_crawled_pages_feed_the_parser_monitor(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The numbers come from real crawl batches, not from a separate bookkeeping path."""
    from app.services import plans

    channel = await crawl_channel(session, "feeder", status="pending")
    cid = channel.id
    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()

    internal = {"Authorization": "Bearer internal-token"}
    claim = await client.post(
        "/internal/indexer/crawl/claim", json={"worker_id": "w", "limit": 5}, headers=internal
    )
    (task,) = claim.json()["tasks"]
    await client.post(
        "/internal/indexer/crawl/batch",
        json={
            "channel_id": cid,
            "lease_token": task["lease_token"],
            "items": [],
            "page_messages": 20,
            "extraction_rate": 0.0,
            "oldest_msg_id": 1,
            "newest_msg_id": 20,
            "finished": True,
        },
        headers=internal,
    )

    row = (
        await session.execute(
            text("SELECT pages, messages, audio_items, empty_pages FROM crawl_stats_daily")
        )
    ).one()
    assert (row.pages, row.messages, row.audio_items, row.empty_pages) == (1, 20, 0, 1)

    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()


# ── resolver status ───────────────────────────────────────────────────────────


async def test_the_resolver_page_reports_the_queue_and_the_breaker(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    resolving.reset_breaker()
    channel = await crawl_channel(session, "tracks_here", status="active")
    from app.services.ingest import ingest_items
    from tests.integration.helpers import BASE_TIME
    from tmusic_common.indexer_contract import AudioItem

    await ingest_items(
        session,
        channel,
        [
            AudioItem(
                message_id=i,
                posted_at=BASE_TIME,
                duration=0,
                file_size=0,
                title=f"Artist - Song {i}",
            )
            for i in (1, 2, 3)
        ],
    )
    await session.commit()
    headers = await owner(client, session)

    status = (await client.get("/admin/crawler/resolver", headers=headers)).json()
    assert status["unresolved"] == 3
    assert status["resolved"] == 0
    assert status["breaker_open"] is False

    for _ in range(resolving.BREAKER_THRESHOLD):
        resolving.breaker.trip()
    opened = (await client.get("/admin/crawler/resolver", headers=headers)).json()
    assert opened["breaker_open"] is True
    assert opened["consecutive_failures"] == resolving.BREAKER_THRESHOLD
    resolving.reset_breaker()


# ── permissions ───────────────────────────────────────────────────────────────


async def test_the_crawler_pages_need_system_view(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_admin(session, 78002, role="support", permissions=["dashboard.view"])
    await session.commit()
    weak = await admin_token(client, 78002)

    for path in ("/admin/crawler", "/admin/crawler/parser", "/admin/crawler/resolver"):
        assert (await client.get(path, headers=weak)).status_code == 403
    assert (await client.post("/admin/candidates/1/approve", headers=weak)).status_code == 403
    assert (
        await client.post("/admin/channels/import", json={"text": "chan"}, headers=weak)
    ).status_code == 403


async def test_importing_channels_from_the_panel(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    headers = await owner(client, session)
    resp = await client.post(
        "/admin/channels/import",
        json={"text": "@one_chan\nhttps://t.me/two_chan\nbroken!!"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 2, "existing": 0, "blocked": 0, "invalid": ["broken!!"]}
    names = set(await session.scalars(select(Channel.username)))
    assert {"one_chan", "two_chan"} <= names
