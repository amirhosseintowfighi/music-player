"""Phase 2, core side: the crawl scheduler and the /internal/indexer/crawl/* contract.

The crawler itself is tested on the edge (indexer/tests/test_crawler.py). What is
tested here is everything that survives a worker dying: leases, cursors, resumability,
the preview_disabled fallback, failure backoff and the adaptive interval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Track
from app.services import crawling, plans
from tests.integration.helpers import BASE_TIME, make_channel
from tmusic_common.indexer_contract import AudioItem

AUTH = {"Authorization": "Bearer internal-token"}


@pytest.fixture(autouse=True)
async def crawler_on(session: AsyncSession) -> Any:
    """The whole feature sits behind a flag; without it nothing is ever claimed."""
    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()
    yield
    await session.rollback()
    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()


async def api(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> Any:
    resp = await client.post(f"/internal/indexer{path}", json=body, headers=AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def crawl_channel(session: AsyncSession, username: str, **values: Any) -> Channel:
    values.setdefault("source_type", "web_preview")
    values.setdefault("status", "pending")
    values.setdefault("next_crawl_at", datetime.now(UTC))
    return await make_channel(session, username, **values)


def crawled(msg: int, title: str = "Someone - Song") -> AudioItem:
    """What the parser produces: a message id and metadata, no file identity."""
    return AudioItem(
        message_id=msg,
        posted_at=BASE_TIME + timedelta(minutes=msg),
        duration=0,
        file_size=0,
        title=title,
        performer=None,
    )


async def claim(client: httpx.AsyncClient, worker: str = "edge-1", limit: int = 5) -> list[Any]:
    out = await api(client, "/crawl/claim", {"worker_id": worker, "limit": limit})
    return list(out["tasks"])


def batch(task: dict[str, Any], items: list[AudioItem], **extra: Any) -> dict[str, Any]:
    body = {
        "channel_id": task["channel_id"],
        "lease_token": task["lease_token"],
        "items": [i.model_dump(mode="json") for i in items],
        "finished": False,
        **extra,
    }
    return body


# ── claiming ──────────────────────────────────────────────────────────────────


async def test_nothing_is_claimed_while_the_flag_is_off(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await crawl_channel(session, "flagoff")
    # Anything but "crawler", with the master switch off, means nobody indexes.
    await session.execute(
        text("UPDATE feature_flags SET value = '\"disabled\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()

    assert await claim(client) == []


async def test_a_claim_leases_the_channel_exclusively(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "exclusive", title=None)
    cid = ch.id
    await session.commit()

    (task,) = await claim(client)
    assert task["mode"] == "backfill"
    assert task["before"] is None  # nothing crawled yet: start at the top
    assert task["needs_meta"] is True

    assert await claim(client, worker="edge-2") == []

    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert (ch.crawl_status, ch.status) == ("running", "indexing")


async def test_unfinished_backfills_outrank_channels_merely_due(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    # Waiting longest, but already fully indexed.
    await crawl_channel(
        session,
        "settled",
        status="active",
        oldest_crawled_msg_id=1,
        newest_crawled_msg_id=900,
        next_crawl_at=datetime.now(UTC) - timedelta(hours=5),
    )
    await crawl_channel(
        session,
        "halfway",
        status="indexing",
        oldest_crawled_msg_id=400,
        newest_crawled_msg_id=900,
        next_crawl_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await session.commit()

    tasks = await claim(client, limit=2)
    assert [t["username"] for t in tasks] == ["halfway", "settled"]
    assert tasks[0]["mode"] == "backfill"
    assert tasks[0]["before"] == 400  # resumes at the stored cursor
    assert tasks[1]["mode"] == "incremental"
    assert tasks[1]["stop_at"] == 900


# ── ingest, cursors, resumability ─────────────────────────────────────────────


async def test_a_page_is_ingested_and_moves_the_cursors(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "pagey", title=None)
    cid = ch.id
    await session.commit()
    (task,) = await claim(client)

    out = await api(
        client,
        "/crawl/batch",
        batch(
            task,
            [crawled(100), crawled(99, "Other - Tune")],
            meta={"username": "pagey", "title": "Pagey", "subscribers": 1200},
            oldest_msg_id=99,
            newest_msg_id=100,
            extraction_rate=1.0,
        ),
    )
    assert out == {"lease_valid": True, "inserted": 2, "updated": 0, "duplicates": 0}

    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert (ch.oldest_crawled_msg_id, ch.newest_crawled_msg_id) == (99, 100)
    assert ch.title == "Pagey"
    assert ch.preview_available is True
    assert ch.last_crawl_at is not None
    assert ch.last_indexed_msg_id == 100  # the old column stays consistent

    tracks = (await session.scalars(select(Track))).all()
    assert {t.resolve_status for t in tracks} == {"unresolved"}
    assert all(t.file_unique_id is None and t.source_key for t in tracks)


async def test_a_killed_job_resumes_from_the_stored_cursor(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """§8: kill the job mid-way, confirm it resumes rather than starting over."""
    ch = await crawl_channel(session, "resumable")
    cid = ch.id
    await session.commit()
    (task,) = await claim(client)

    # Two pages land, then the worker disappears without a final batch.
    await api(
        client, "/crawl/batch", batch(task, [crawled(60)], oldest_msg_id=41, newest_msg_id=60)
    )
    await api(
        client, "/crawl/batch", batch(task, [crawled(30)], oldest_msg_id=21, newest_msg_id=60)
    )
    await session.execute(
        update(Channel).where(Channel.id == cid).values(lease_until=datetime.now(UTC))
    )
    await session.commit()
    plans.clear_caches()

    (resumed,) = await claim(client, worker="edge-2")
    assert resumed["before"] == 21  # not None — the first 40 messages are not re-read
    assert resumed["mode"] == "backfill"
    assert resumed["needs_meta"] is False

    # And the page posted under the dead lease is refused.
    stale = await api(client, "/crawl/batch", batch(task, [crawled(10)], oldest_msg_id=1))
    assert stale["lease_valid"] is False
    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert ch.oldest_crawled_msg_id == 21  # the stale page did not move it


async def test_finishing_a_backfill_switches_to_adaptive_updates(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "done")
    cid = ch.id
    await session.commit()
    (task,) = await claim(client)

    await api(
        client,
        "/crawl/batch",
        batch(task, [crawled(2), crawled(1)], oldest_msg_id=1, newest_msg_id=2, finished=True),
    )
    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert (ch.status, ch.crawl_status, ch.progress_pct) == ("active", "idle", 100)
    assert ch.lease_owner is None
    assert crawling.MIN_INTERVAL_S <= ch.crawl_interval_sec <= crawling.MAX_INTERVAL_S
    assert ch.next_crawl_at > datetime.now(UTC)


async def test_re_crawling_the_first_page_updates_rather_than_duplicates(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "incr")
    await session.commit()
    (task,) = await claim(client)
    await api(
        client,
        "/crawl/batch",
        batch(task, [crawled(5)], oldest_msg_id=1, newest_msg_id=5, finished=True),
    )
    await session.execute(
        update(Channel).where(Channel.id == ch.id).values(next_crawl_at=datetime.now(UTC))
    )
    await session.commit()

    (task2,) = await claim(client)
    assert (task2["mode"], task2["stop_at"]) == ("incremental", 5)
    out = await api(
        client,
        "/crawl/batch",
        batch(task2, [crawled(5), crawled(6)], oldest_msg_id=5, newest_msg_id=6, finished=True),
    )
    assert (out["inserted"], out["updated"]) == (1, 1)
    assert await session.scalar(select(text("count(*)")).select_from(Track)) == 2


# ── mentions become candidates ────────────────────────────────────────────────


async def test_mentions_found_while_crawling_become_pending_candidates(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "source_chan")
    cid = ch.id
    await crawl_channel(
        session,
        "already_known",
        status="active",
        next_crawl_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await session.commit()
    (task,) = await claim(client)
    assert task["username"] == "source_chan"

    await api(
        client,
        "/crawl/batch",
        batch(
            task,
            [crawled(1)],
            oldest_msg_id=1,
            newest_msg_id=1,
            finished=True,
            mentions=["@NewOne", "newone", "already_known", "x"],
        ),
    )
    rows = (
        await session.execute(text("SELECT username, source, status FROM channel_candidates"))
    ).all()
    # Deduplicated, lowercased, existing channels and too-short names dropped.
    assert [(r.username, r.source, r.status) for r in rows] == [
        ("newone", "crawl_mention", "pending")
    ]
    assert (
        await session.scalar(
            text("SELECT discovered_from_channel_id FROM channel_candidates LIMIT 1")
        )
        == cid
    )


# ── failure paths ─────────────────────────────────────────────────────────────


async def test_a_channel_without_preview_is_marked_not_silently_empty(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "nopreview")
    cid = ch.id
    await session.commit()
    (task,) = await claim(client)

    out = await api(
        client,
        f"/crawl/channels/{cid}/failure",
        {
            "lease_token": task["lease_token"],
            "reason": "preview_disabled",
            "detail": "redirected to t.me",
            "preview_disabled": True,
        },
    )
    assert out == {"recorded": True}
    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert ch.crawl_status == "preview_disabled"
    assert ch.preview_available is False
    assert ch.status_reason == "preview_disabled"
    assert ch.crawl_error == "redirected to t.me"
    assert ch.next_crawl_at > datetime.now(UTC) + timedelta(hours=12)
    assert await claim(client) == []  # and it is out of the rotation


async def test_repeated_failures_back_off_and_eventually_stop(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "broken")
    cid = ch.id
    await session.commit()

    for _ in range(crawling.MAX_FAILURES):
        await session.execute(
            update(Channel).where(Channel.id == cid).values(next_crawl_at=datetime.now(UTC))
        )
        await session.commit()
        (task,) = await claim(client)
        await api(
            client,
            f"/crawl/channels/{cid}/failure",
            {"lease_token": task["lease_token"], "reason": "crawl_error", "detail": "boom"},
        )

    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert (ch.crawl_status, ch.status) == ("error", "failed")
    assert ch.fail_count == crawling.MAX_FAILURES
    await session.execute(
        update(Channel).where(Channel.id == cid).values(next_crawl_at=datetime.now(UTC))
    )
    await session.commit()
    assert await claim(client) == []  # stops costing requests


async def test_a_blocked_worker_gives_the_channel_straight_back(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    ch = await crawl_channel(session, "blocked")
    cid = ch.id
    await session.commit()
    (task,) = await claim(client)

    assert (
        await api(
            client,
            f"/crawl/channels/{cid}/release",
            {"lease_token": task["lease_token"], "retry_after_s": 300},
        )
    )["released"] is True

    session.expire_all()
    ch = (await session.scalars(select(Channel).where(Channel.id == cid))).one()
    assert (ch.crawl_status, ch.lease_owner, ch.fail_count) == ("idle", None, 0)
    assert ch.next_crawl_at > datetime.now(UTC) + timedelta(minutes=4)

    # A release with the wrong token changes nothing.
    assert (await api(client, f"/crawl/channels/{cid}/release", {"lease_token": "nope"}))[
        "released"
    ] is False


# ── scheduling and health ─────────────────────────────────────────────────────


async def test_the_interval_follows_the_channels_real_posting_rate(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    quiet = await crawl_channel(session, "quiet")
    busy = await crawl_channel(session, "busy")
    await session.commit()

    assert await crawling.next_interval(session, quiet) == crawling.MAX_INTERVAL_S

    # A channel posting ~27 tracks a day deserves more than the daily ceiling.
    busy_id = busy.id
    (task,) = [t for t in await claim(client) if t["username"] == "busy"]
    now = datetime.now(UTC)
    for start in range(1, 801, 200):
        ids = range(start, start + 200)
        await api(
            client,
            "/crawl/batch",
            batch(
                task,
                [
                    AudioItem(
                        message_id=i,
                        posted_at=now - timedelta(minutes=i),
                        duration=0,
                        file_size=0,
                        title=f"Artist - Song {i}",
                    )
                    for i in ids
                ],
                oldest_msg_id=start,
                newest_msg_id=start + 199,
                finished=start == 601,
            ),
        )
    session.expire_all()
    busy = (await session.scalars(select(Channel).where(Channel.id == busy_id))).one()
    assert crawling.MIN_INTERVAL_S <= busy.crawl_interval_sec < crawling.MAX_INTERVAL_S


async def test_health_counts_what_the_admin_page_shows(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await crawl_channel(session, "h_running", crawl_status="running")
    await crawl_channel(session, "h_error", crawl_status="error")
    await crawl_channel(session, "h_nopreview", crawl_status="preview_disabled")
    await crawl_channel(session, "h_done", status="active", crawl_status="idle")
    await session.commit()

    health = await crawling.health(session)
    assert health["channels"] >= 4
    assert health["running"] == 1
    assert health["errored"] == 1
    assert health["preview_disabled"] == 1
    assert health["completed"] >= 1
    assert health["unresolved_tracks"] == 0


# ── channel metadata (survives from the old path, still used by every crawl) ───


async def test_meta_merges_two_rows_that_turn_out_to_be_one_channel(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The same channel added twice — once by username, once by a forwarded post.

    Only the resolver ever learns the numeric id, so the merge happens whenever it
    finally arrives, not at crawl time.
    """
    from app.models import UserChannel
    from app.services import crawling as crawl_service
    from tmusic_common.indexer_contract import ChannelMeta

    by_username = await crawl_channel(session, "dupe_name", status="active")
    by_id = await crawl_channel(session, "dupe_id", status="active", tg_channel_id=9001)
    keep_id, drop_id = by_id.id, by_username.id
    from tests.integration.test_social import make_user

    user_ids = [(await make_user(session, tg)).id for tg in (5551, 5552)]
    await session.execute(
        text(
            "INSERT INTO user_channels (user_id, channel_id) VALUES (:a, :d), (:b, :k)"
        ).bindparams(a=user_ids[0], b=user_ids[1], d=drop_id, k=keep_id)
    )
    await session.flush()

    merged = await crawl_service.apply_meta(
        session,
        by_username,
        ChannelMeta(tg_channel_id=9001, username="dupe_id", title="One Channel"),
    )
    assert merged.id == keep_id
    assert await session.scalar(select(Channel.id).where(Channel.id == drop_id)) is None
    # The subscriber of the dropped row keeps the channel.
    subscribers = set(
        await session.scalars(select(UserChannel.user_id).where(UserChannel.channel_id == keep_id))
    )
    assert subscribers == set(user_ids)
    assert merged.subscribers_count == 2


async def test_meta_from_the_crawler_has_no_numeric_id_and_merges_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    from app.services import crawling as crawl_service
    from tmusic_common.indexer_contract import ChannelMeta

    await crawl_channel(session, "other_one", status="active", tg_channel_id=9002)
    channel = await crawl_channel(session, "crawled_one", title=None)
    cid = channel.id

    same = await crawl_service.apply_meta(
        session,
        channel,
        ChannelMeta(username="crawled_one", title="Crawled One", subscribers=500),
    )
    assert same.id == cid  # nothing to merge on
    assert same.tg_channel_id is None
    assert same.title == "Crawled One"


async def test_meta_does_not_steal_a_username_another_channel_holds(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    from app.services import crawling as crawl_service
    from tmusic_common.indexer_contract import ChannelMeta

    await crawl_channel(session, "taken_name", status="active")
    channel = await crawl_channel(session, "renamed_chan")

    result = await crawl_service.apply_meta(
        session, channel, ChannelMeta(username="taken_name", title="Renamed")
    )
    assert result.username == "renamed_chan"  # the other row keeps the name
    assert result.title == "Renamed"


async def test_a_bot_administered_channel_is_never_queued_for_the_web_crawler(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Layer B posts to us directly; its preview may not even exist (ADR-002 §2)."""
    from app.services.channels import ChannelRef, get_or_create_channel

    channel, created = await get_or_create_channel(
        session,
        ChannelRef(username="botowned", tg_channel_id=-100777, title="Bot Owned"),
        added_by=None,
        source="bot_admin",
    )
    assert created
    assert channel.source_type == "bot_member"
    assert channel.status == "active"
    await session.execute(
        text("UPDATE channels SET next_crawl_at = now() WHERE id = :i").bindparams(i=channel.id)
    )
    await session.commit()

    assert await claim(client) == []  # the crawler leaves it alone
