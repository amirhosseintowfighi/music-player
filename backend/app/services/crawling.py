"""Scheduling side of the web-preview crawler (ADR-002).

This is the MTProto claim/lease machinery with the account pool removed. What is left
is what actually mattered: one channel is crawled by one worker at a time, cursors are
persisted after every page, and a worker that dies loses at most one page of progress.

The scheduler is a priority queue, not a fixed cron:

- a channel still backfilling outranks one that is merely due for an update
- among due channels, the one waiting longest goes first
- the gap until the next look is derived from how fast the channel actually posts
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound
from app.models import Channel, UserChannel
from app.services import plans
from tmusic_common.indexer_contract import ChannelMeta
from tmusic_common.logging import get_logger

log = get_logger(__name__)

LEASE_S = 900
# The floor and ceiling of the adaptive interval, mirrored from the crawler.
MIN_INTERVAL_S = 900
MAX_INTERVAL_S = 86_400
PAGE_SIZE = 20
# A channel that fails this many times in a row stops being asked about.
MAX_FAILURES = 5


@dataclass(frozen=True, slots=True)
class CrawlTask:
    channel_id: int
    username: str
    lease_token: str
    mode: str  # "backfill" | "incremental"
    before: int | None
    stop_at: int | None
    needs_meta: bool
    # Which reader the edge should use: the web preview, or the logged-in account for
    # channels that have no preview at all.
    source: str = "web_preview"


async def enabled(session: AsyncSession) -> bool:
    """The feature flag, whose default is now the crawler (migration 0009).

    It used to default to the MTProto pool — the code that phase 6 deleted — which
    made "no flag" mean "index with nothing" and left added channels sitting in
    ``indexing`` forever, silently.
    """
    source = await plans.get_flag(session, "indexing_source", "crawler")
    return bool(await plans.get_flag(session, "crawler_enabled", False)) or source == "crawler"


async def claim(
    session: AsyncSession, worker_id: str, limit: int = 5, lease_s: int = LEASE_S
) -> list[CrawlTask]:
    """Hands out channels to crawl, locking each one for this worker."""
    if not await enabled(session):
        return []

    # A worker that dies mid-crawl leaves its channel marked "running", and nothing
    # ever looked at it again: the claim below only considers idle and error, so an
    # edge restart quietly froze every channel it happened to hold. Leases exist
    # precisely so that a dead worker's work returns — this is where it returns.
    reclaimed = await session.execute(
        text(
            """
        UPDATE channels
           SET crawl_status = 'idle', lease_owner = NULL, lease_until = NULL
         WHERE crawl_status = 'running'
           AND (lease_until IS NULL OR lease_until < now())
        RETURNING id
        """
        )
    )
    orphans = [row[0] for row in reclaimed]
    if orphans:
        log.info("crawl.leases_reclaimed", channels=orphans)

    # Channels whose preview is off are only handed out once an operator has turned
    # the account-based fallback on; until then they stay parked, not retried.
    readable = ["web_preview"]
    fallback = bool(await plans.get_flag(session, "mtproto_fallback", False))
    # Turning the flag on also un-parks everything that already gave up on the web
    # preview, so an operator does not have to press "recrawl" on each of them.
    crawl_states = ["idle", "error", "preview_disabled"] if fallback else ["idle", "error"]
    if fallback:
        readable.append("mtproto")

    due = (
        await session.scalars(
            select(Channel)
            .where(
                Channel.username.is_not(None),
                Channel.source_type.in_(readable),
                Channel.status.in_(("pending", "indexing", "active")),
                Channel.crawl_status.in_(crawl_states),
                Channel.next_crawl_at <= func.now(),
                (Channel.lease_until.is_(None)) | (Channel.lease_until < func.now()),
                Channel.fail_count < MAX_FAILURES,
            )
            # Unfinished backfills first, then whoever has waited longest.
            .order_by((Channel.status == "active").asc(), Channel.next_crawl_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()

    lease_until = datetime.now(UTC) + timedelta(seconds=lease_s)
    tasks: list[CrawlTask] = []
    for channel in due:
        token = secrets.token_hex(16)
        if channel.crawl_status == "preview_disabled":
            # It only got here because the fallback is on; the account reads it now.
            channel.source_type = "mtproto"
            channel.fail_count = 0
        channel.lease_owner = f"{worker_id}:{token}"
        channel.lease_until = lease_until
        channel.crawl_status = "running"
        if channel.status == "pending":
            channel.status = "indexing"

        backfilling = channel.oldest_crawled_msg_id is None or channel.oldest_crawled_msg_id > 1
        tasks.append(
            CrawlTask(
                channel_id=channel.id,
                username=channel.username or "",
                lease_token=token,
                mode="backfill" if backfilling else "incremental",
                # Resume exactly where the last run stopped.
                before=channel.oldest_crawled_msg_id if backfilling else None,
                stop_at=None if backfilling else channel.newest_crawled_msg_id,
                needs_meta=channel.title is None,
                source=channel.source_type,
            )
        )
    await session.flush()
    log.info("crawl.claimed", worker=worker_id, channels=[t.channel_id for t in tasks])
    return tasks


def _lease_ok(channel: Channel, token: str) -> bool:
    return (
        channel.lease_owner is not None
        and channel.lease_owner.rsplit(":", 1)[-1] == token
        and channel.lease_until is not None
        and channel.lease_until > datetime.now(UTC)
    )


async def _locked(session: AsyncSession, channel_id: int) -> Channel:
    channel = (
        await session.scalars(select(Channel).where(Channel.id == channel_id).with_for_update())
    ).one_or_none()
    if channel is None:
        raise NotFound("channel not found")
    return channel


async def channel_for_batch(
    session: AsyncSession, channel_id: int, lease_token: str
) -> Channel | None:
    """The channel this batch belongs to, or None when the lease has expired."""
    channel = await _locked(session, channel_id)
    return channel if _lease_ok(channel, lease_token) else None


async def record_page_health(
    session: AsyncSession, channel: Channel, *, messages: int, items: int, rate: float | None
) -> None:
    """One page's yield, rolled up per day (ADR-002 §5, the parser monitor).

    Telegram's preview markup is an unofficial contract. Nobody will announce a change
    to it; the only warning is the share of messages that still produce a track, so
    every page updates a daily counter the health check compares against last week.
    """
    if messages <= 0:
        return
    if rate is not None:
        channel.extraction_rate = rate
    await session.execute(
        text(
            """
        INSERT INTO crawl_stats_daily (day, pages, messages, audio_items, empty_pages)
        VALUES (current_date, 1, :messages, :items, :empty)
        ON CONFLICT (day) DO UPDATE SET
            pages       = crawl_stats_daily.pages + 1,
            messages    = crawl_stats_daily.messages + excluded.messages,
            audio_items = crawl_stats_daily.audio_items + excluded.audio_items,
            empty_pages = crawl_stats_daily.empty_pages + excluded.empty_pages,
            updated_at  = now()
        """
        ).bindparams(messages=messages, items=items, empty=1 if items == 0 else 0)
    )


async def save_progress(
    session: AsyncSession,
    channel_id: int,
    lease_token: str,
    *,
    oldest: int | None,
    newest: int | None,
    finished: bool,
    extraction_rate: float | None = None,
) -> bool:
    """Persists cursors after a page. Returns False when the lease is gone.

    Called after **every** page, which is what makes a crashed job cheap: the next run
    resumes one page back, not at the beginning of the channel.
    """
    channel = await _locked(session, channel_id)
    if not _lease_ok(channel, lease_token):
        log.info("crawl.lease_lost", channel_id=channel_id)
        return False

    if oldest is not None:
        channel.oldest_crawled_msg_id = (
            oldest
            if channel.oldest_crawled_msg_id is None
            else min(channel.oldest_crawled_msg_id, oldest)
        )
    if newest is not None:
        channel.newest_crawled_msg_id = (
            newest
            if channel.newest_crawled_msg_id is None
            else max(channel.newest_crawled_msg_id, newest)
        )
        # The old column keeps working so the MTProto path and the UI stay consistent.
        channel.last_indexed_msg_id = max(channel.last_indexed_msg_id, newest)

    channel.preview_available = True
    channel.last_crawl_at = datetime.now(UTC)
    channel.crawl_error = None

    if finished:
        await _finish(session, channel)
    else:
        # Still work to do: keep the lease alive and come back promptly.
        channel.crawl_status = "idle"
        channel.next_crawl_at = datetime.now(UTC)
        channel.progress_pct = _progress_pct(channel)
    if extraction_rate is not None:
        log.info(
            "crawl.progress",
            channel_id=channel_id,
            oldest=channel.oldest_crawled_msg_id,
            newest=channel.newest_crawled_msg_id,
            rate=round(extraction_rate, 3),
        )
    return True


def _progress_pct(channel: Channel) -> int:
    """How far back we have walked, as a share of the channel's message range."""
    newest = channel.newest_crawled_msg_id
    oldest = channel.oldest_crawled_msg_id
    if not newest or not oldest or newest <= 1:
        return 0
    walked = newest - oldest + 1
    return max(0, min(100, int(100 * walked / newest)))


async def _finish(session: AsyncSession, channel: Channel) -> None:
    """Backfill complete: switch to adaptive incremental updates."""
    channel.crawl_status = "idle"
    channel.status = "active"
    channel.lease_owner = None
    channel.lease_until = None
    channel.fail_count = 0
    channel.progress_pct = 100
    channel.crawl_interval_sec = await next_interval(session, channel)
    channel.next_crawl_at = datetime.now(UTC) + timedelta(seconds=channel.crawl_interval_sec)
    channel.last_indexed_at = datetime.now(UTC)


async def next_interval(session: AsyncSession, channel: Channel) -> int:
    """Seconds until this channel is worth reading again.

    Derived from its real posting rate: roughly the time it takes to produce one
    preview page of new messages, clamped to the 15-minute floor and daily ceiling.
    """
    rate = await session.scalar(
        text(
            "SELECT count(*)::float / 30 FROM channel_tracks"
            " WHERE channel_id = :id AND posted_at > now() - interval '30 days'"
        ).bindparams(id=channel.id)
    )
    per_day = float(rate or 0.0)
    if per_day <= 0:
        return MAX_INTERVAL_S
    seconds_per_page = PAGE_SIZE * 86_400 / per_day
    return int(min(max(seconds_per_page, MIN_INTERVAL_S), MAX_INTERVAL_S))


async def release(
    session: AsyncSession, channel_id: int, lease_token: str, retry_after_s: int = 60
) -> bool:
    """Gives a channel back without progress — a blocked IP, a restart, a timeout."""
    channel = await _locked(session, channel_id)
    if not _lease_ok(channel, lease_token):
        return False
    channel.lease_owner = None
    channel.lease_until = None
    channel.crawl_status = "idle"
    channel.next_crawl_at = datetime.now(UTC) + timedelta(seconds=retry_after_s)
    return True


async def report_failure(
    session: AsyncSession,
    channel_id: int,
    lease_token: str,
    *,
    reason: str,
    detail: str = "",
    preview_disabled: bool = False,
    permanent: bool = False,
) -> bool:
    """A channel that cannot be crawled. Never silently empty — always a status."""
    channel = await _locked(session, channel_id)
    if not _lease_ok(channel, lease_token):
        return False

    channel.lease_owner = None
    channel.lease_until = None
    channel.crawl_error = detail[:500] or reason
    channel.fail_count += 1

    if permanent:
        # A username nobody owns, or one that belongs to a person or a bot. It will
        # not start existing, so five retries with growing backoff spend the account's
        # attention on nothing — and fill the crawler page with noise that hides the
        # failures worth reading.
        channel.crawl_status = "error"
        channel.status = "failed"
        channel.status_reason = reason
        channel.fail_count = MAX_FAILURES
        channel.next_crawl_at = datetime.now(UTC) + timedelta(days=365)
        log.info("crawl.permanent_failure", channel_id=channel_id, username=channel.username)
    elif preview_disabled:
        channel.preview_available = False
        channel.status_reason = "preview_disabled"
        if await plans.get_flag(session, "mtproto_fallback", False):
            # Hand it to the account instead of parking it. The web path is never
            # retried for this channel afterwards: the preview is a channel setting,
            # not a transient failure, so asking again daily just burns requests.
            channel.source_type = "mtproto"
            channel.crawl_status = "idle"
            channel.fail_count = 0
            channel.next_crawl_at = datetime.now(UTC)
            log.info("crawl.handed_to_mtproto", channel_id=channel_id, username=channel.username)
        else:
            channel.crawl_status = "preview_disabled"
            channel.next_crawl_at = datetime.now(UTC) + timedelta(days=1)
            log.warning("crawl.preview_disabled", channel_id=channel_id, username=channel.username)
    else:
        channel.crawl_status = "error"
        # Exponential, so a channel that is simply gone stops costing requests.
        backoff = min(2**channel.fail_count, 720)
        channel.next_crawl_at = datetime.now(UTC) + timedelta(minutes=backoff)
        if channel.fail_count >= MAX_FAILURES:
            channel.status = "failed"
            channel.status_reason = reason
    return True


async def health(session: AsyncSession) -> dict[str, Any]:
    """What the admin panel's crawler page shows (phase 5) and the alert watches."""
    row = (
        await session.execute(
            text(
                """
        SELECT
          count(*) FILTER (WHERE source_type IN ('web_preview', 'mtproto'))  AS channels,
          count(*) FILTER (WHERE crawl_status = 'running')                   AS running,
          count(*) FILTER (WHERE crawl_status = 'error')                     AS errored,
          count(*) FILTER (WHERE crawl_status = 'preview_disabled')          AS no_preview,
          count(*) FILTER (WHERE source_type IN ('web_preview', 'mtproto')
                             AND status = 'active')                          AS done,
          count(*) FILTER (WHERE source_type = 'mtproto')                    AS mtproto,
          count(*) FILTER (WHERE next_crawl_at <= now()
                             AND crawl_status IN ('idle', 'error'))          AS due
          FROM channels
        """
            )
        )
    ).one()
    unresolved = await session.scalar(
        text("SELECT count(*) FROM tracks WHERE resolve_status = 'unresolved'")
    )
    return {
        "channels": int(row.channels),
        "running": int(row.running),
        "errored": int(row.errored),
        "preview_disabled": int(row.no_preview),
        "mtproto": int(row.mtproto),
        "completed": int(row.done),
        "due": int(row.due),
        "unresolved_tracks": int(unresolved or 0),
    }


async def apply_meta(session: AsyncSession, channel: Channel, meta: ChannelMeta) -> Channel:
    """Store resolved metadata. If the Telegram id already belongs to another row (added
    once by username and once by a forwarded post), fold this row into that one."""
    # The crawler cannot see the numeric id, so there is nothing to merge on then.
    other = (
        (
            await session.scalars(
                select(Channel).where(
                    Channel.tg_channel_id == meta.tg_channel_id, Channel.id != channel.id
                )
            )
        ).one_or_none()
        if meta.tg_channel_id is not None
        else None
    )
    if other is not None:
        await session.execute(
            text(
                "INSERT INTO user_channels (user_id, channel_id, added_at) "
                "SELECT user_id, :keep, added_at FROM user_channels WHERE channel_id = :drop "
                "ON CONFLICT DO NOTHING"
            ).bindparams(keep=other.id, drop=channel.id)
        )
        other.subscribers_count = (
            await session.scalar(
                select(func.count())
                .select_from(UserChannel)
                .where(UserChannel.channel_id == other.id)
            )
        ) or 0
        await session.delete(channel)
        await session.flush()
        log.info("indexer.channel_merged", kept=other.id, dropped=channel.id)
        return other
    if meta.tg_channel_id is not None:
        channel.tg_channel_id = meta.tg_channel_id
    if meta.username and meta.username.lower() != (channel.username or "").lower():
        taken = await session.scalar(
            select(Channel.id).where(Channel.username == meta.username, Channel.id != channel.id)
        )
        if taken is None:
            channel.username = meta.username
    channel.title = meta.title[:256]
    channel.description = (meta.description or "")[:2000] or None
    if meta.music_total is not None:
        channel.backfill_total_estimate = meta.music_total
    return channel


# ── parser health (ADR-002 §2, layer A) ───────────────────────────────────────
#
# Telegram's preview markup is an unofficial contract that will change without
# warning, and the failure is silent: pages still parse, they just stop yielding
# tracks. The only signal is the share of messages that produce an item, compared
# with the days before.

PARSER_ALERT_DROP = 0.5
# Below this many messages a day the numbers are noise, not evidence.
PARSER_MIN_SAMPLE = 20


async def parser_health(session: AsyncSession, days: int = 14) -> dict[str, Any]:
    """Extraction rate per day, plus the one question that matters: did it collapse?"""
    rows = (
        await session.execute(
            text(
                """
        SELECT day, pages, messages, audio_items, empty_pages,
               CASE WHEN messages > 0 THEN audio_items::float / messages ELSE 0 END AS rate
          FROM crawl_stats_daily
         WHERE day > current_date - CAST(:days AS int)
         ORDER BY day DESC
        """
            ).bindparams(days=days)
        )
    ).mappings()
    history = [dict(row) for row in rows]
    today = history[0] if history else None
    baseline = [row for row in history[1:] if row["messages"] >= PARSER_MIN_SAMPLE]
    expected = sum(r["rate"] for r in baseline) / len(baseline) if baseline else None
    dropped = bool(
        today
        and expected
        and today["messages"] >= PARSER_MIN_SAMPLE
        and today["rate"] < expected * PARSER_ALERT_DROP
    )
    return {
        "days": history,
        "today_rate": round(today["rate"], 4) if today else None,
        "expected_rate": round(expected, 4) if expected else None,
        "alert": dropped,
    }
