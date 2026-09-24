"""Periodic jobs. Each takes the arq context dict and is safe to run concurrently or twice."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import metrics
from app.bot.setup import build_bot
from app.bot.texts import t
from app.config import get_settings
from app.db import session_scope
from app.models import EdgeNode, User
from app.redis_util import resolve
from app.services import (
    artistinfo,
    broadcast,
    crawling,
    metadata,
    notifications,
    recommendations,
    resolving,
    search,
    social,
    subscriptions,
    warming,
)
from app.services.meili import MeiliClient
from app.services.stream import edge_base_url
from tmusic_common.logging import get_logger

log = get_logger(__name__)

Ctx = dict[str, Any]


def _maker(ctx: Ctx) -> async_sessionmaker[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    return maker


async def sync_search(ctx: Ctx) -> int:
    redis: Redis = ctx["redis_app"]
    meili: MeiliClient = ctx["meili"]
    # One syncer at a time across all worker replicas.
    if not await redis.set("lock:sync_search", "1", nx=True, ex=60):
        return 0
    total = 0
    try:
        async with session_scope(_maker(ctx)) as session:
            while (n := await search.sync_changes(session, meili, redis)) > 0:
                total += n
    finally:
        await redis.delete("lock:sync_search")
    if total:
        log.info("search.synced", rows=total)
    return total


async def refresh_track_counters(ctx: Ctx) -> None:
    """likes_count / plays_7d are denormalised; only rows whose value changed are written."""
    async with session_scope(_maker(ctx)) as session:
        for column, source in (
            (
                "plays_7d",
                "SELECT track_id, count(*)::int AS n FROM play_history "
                "WHERE played_at > now() - interval '7 days' GROUP BY track_id",
            ),
            ("likes_count", "SELECT track_id, count(*)::int AS n FROM likes GROUP BY track_id"),
        ):
            await session.execute(
                text(
                    f"WITH c AS ({source}) UPDATE tracks t SET {column} = c.n "
                    f"FROM c WHERE t.id = c.track_id AND t.{column} <> c.n"
                )
            )
            await session.execute(
                text(
                    f"WITH c AS ({source}) UPDATE tracks t SET {column} = 0 "
                    f"WHERE t.{column} <> 0 "
                    "AND NOT EXISTS (SELECT 1 FROM c WHERE c.track_id = t.id)"
                )
            )


async def edge_healthcheck(ctx: Ctx) -> None:
    """Probe every edge from inside the core network; unhealthy edges leave rotation."""
    http: httpx.AsyncClient = ctx["http"]
    async with session_scope(_maker(ctx)) as session:
        nodes = (await session.scalars(select(EdgeNode).where(EdgeNode.is_enabled))).all()
        for node in nodes:
            start = time.perf_counter()
            try:
                resp = await http.get(f"{edge_base_url(node.host)}/healthz", timeout=5.0)
                ok = resp.status_code == 200
            except httpx.HTTPError:
                ok = False
            rtt = int((time.perf_counter() - start) * 1000)
            if ok != node.healthy:
                log.warning("edge.health_changed", host=node.host, healthy=ok)
            await session.execute(
                update(EdgeNode)
                .where(EdgeNode.id == node.id)
                .values(healthy=ok, last_rtt_ms=rtt, last_check_at=func.now())
            )


async def maintenance_daily(ctx: Ctx) -> None:
    async with session_scope(_maker(ctx)) as session:
        await session.execute(text("SELECT ensure_month_partitions('play_history', 2)"))
        await session.execute(text("SELECT ensure_month_partitions('audit_log', 2)"))
        await session.execute(
            text("DELETE FROM refresh_tokens WHERE expires_at < now() - interval '7 days'")
        )
        await session.execute(text("UPDATE indexer_accounts SET floodwait_24h_s = 0"))
        await session.execute(
            text(
                "DELETE FROM search_history WHERE id IN (SELECT id FROM (SELECT id, row_number() "
                "OVER (PARTITION BY user_id ORDER BY created_at DESC) AS rn FROM search_history) s "
                "WHERE rn > 100)"
            )
        )


async def ensure_search_index(ctx: Ctx) -> None:
    meili: MeiliClient = ctx["meili"]
    await meili.ensure_index()


async def reindex_search(ctx: Ctx) -> int:
    """Manual full rebuild: `arq` enqueue from the admin panel or a shell."""
    async with session_scope(_maker(ctx)) as session:
        return await search.full_reindex(session, ctx["meili"], ctx["redis_app"])


async def _notify(bot: Bot, tg_id: int, text_: str) -> bool:
    """Best-effort DM. A blocked bot must never fail the job."""
    try:
        await bot.send_message(tg_id, text_)
        return True
    except TelegramForbiddenError:
        return False
    except TelegramRetryAfter as exc:
        # Telegram's own rate limit: wait exactly as told, never retry immediately.
        await asyncio.sleep(exc.retry_after)
        return False
    except TelegramAPIError:
        log.info("notify.failed", tg_id=tg_id)
        return False


async def subscriptions_daily(ctx: Ctx) -> dict[str, int]:
    """Expiry notices (7/3/1 days), grace, downgrade and stale card-to-card reviews.

    Every branch is idempotent: notices are deduplicated by ``subscription_notices`` and
    the state transitions only ever move forward, so a re-run changes nothing.
    """
    settings = get_settings()
    bot = build_bot(settings)
    sent = graced = downgraded = expired_reviews = 0
    try:
        async with session_scope(_maker(ctx)) as session:
            for subscription_id, tg_id, days, lang in await subscriptions.due_for_notice(session):
                if await _notify(bot, tg_id, t("expiry_notice", lang, days=days)):
                    sent += 1
                # Marked either way: a blocked user must not be retried every hour.
                await subscriptions.mark_notice_sent(session, subscription_id, f"d{days}")
                await asyncio.sleep(0.05)  # ~20 messages/s, well under the Bot API limit

            graced, downgraded = await subscriptions.expire_due(session)
            stale = await subscriptions.expire_stale_reviews(session)
            expired_reviews = len(stale)
            if stale:
                rows = await session.execute(
                    select(User.tg_id, User.lang).where(User.id.in_(stale))
                )
                for tg_id, lang in rows:
                    await _notify(bot, tg_id, t("c2c_expired", lang))
    finally:
        await bot.session.close()
    log.info(
        "subscriptions.daily",
        notices=sent,
        graced=graced,
        downgraded=downgraded,
        expired_reviews=expired_reviews,
    )
    return {
        "notices": sent,
        "graced": graced,
        "downgraded": downgraded,
        "expired_reviews": expired_reviews,
    }


async def recommendations_nightly(ctx: Ctx) -> dict[str, int]:
    """Rebuilds the similarity matrix and the trending snapshots.

    Both are full rebuilds inside one transaction, so readers never see a half-built
    matrix. Generated mixes are a separate job because they are per-user and slower.
    """
    async with session_scope(_maker(ctx)) as session:
        similar = await recommendations.rebuild_similarity(session)
        trending = await recommendations.rebuild_trending(session)
        pruned = await recommendations.prune_generated(session)
        return {"similar": similar, "trending": trending, "pruned": pruned}


async def generate_mixes(ctx: Ctx) -> dict[str, int]:
    """Per-user Discover Weekly and Daily Mixes for everyone seen in the last 30 days.

    Each user is committed on their own: one failure must not lose the whole run.
    """
    maker = _maker(ctx)
    async with session_scope(maker) as session:
        user_ids = await recommendations.active_user_ids(session)
    done = failed = 0
    for user_id in user_ids:
        try:
            async with session_scope(maker) as session:
                await recommendations.refresh_for_user(session, user_id)
            done += 1
        except Exception:
            failed += 1
            log.warning("recs.user_failed", user_id=user_id, exc_info=True)
    async with session_scope(maker) as session:
        await notifications.queue_discover_ready(session, user_ids[:done])
    log.info("recs.mixes_generated", users=done, failed=failed)
    return {"users": done, "failed": failed}


async def run_broadcasts(ctx: Ctx) -> dict[str, int]:
    """Sends whatever broadcast is due. One at a time: the rate limit is per bot."""
    settings = get_settings()
    maker = _maker(ctx)
    async with session_scope(maker) as session:
        due = await broadcast.due_broadcasts(session)
    if not due:
        return {"broadcasts": 0}
    bot = build_bot(settings)
    done = 0
    try:
        for broadcast_id in due:
            await broadcast.run(maker, bot, settings, broadcast_id)
            done += 1
    finally:
        await bot.session.close()
    return {"broadcasts": done}


async def export_business_metrics(ctx: Ctx) -> dict[str, int]:
    """Refreshes the Prometheus gauges. Cheap aggregate queries, once a minute.

    The API process must never run these: they are full-table counts, and the whole
    point of a gauge job is to keep that cost off the request path.
    """
    async with session_scope(_maker(ctx)) as session:
        row = (
            await session.execute(
                text(
                    """
            SELECT
              (SELECT count(*) FROM users)                                            AS users,
              (SELECT count(*) FROM users WHERE last_seen_at > now() - interval '1 day') AS dau,
              (SELECT count(*) FROM tracks WHERE canonical_track_id IS NULL AND NOT hidden)
                                                                                      AS tracks,
              (SELECT count(*) FROM channels WHERE status = 'indexing')               AS indexing,
              (SELECT count(*) FROM payments WHERE status = 'pending_review')         AS to_review,
              (SELECT count(*) FROM reports WHERE status = 'open')                    AS reports
            """
                )
            )
        ).one()
        metrics.USERS_TOTAL.set(row.users)
        metrics.DAU.set(row.dau)
        metrics.TRACKS_TOTAL.set(row.tracks)
        metrics.CHANNELS_INDEXING.set(row.indexing)
        metrics.PAYMENTS_AWAITING_REVIEW.set(row.to_review)
        metrics.OPEN_REPORTS.set(row.reports)

        plans_rows = await session.execute(
            text(
                "SELECT plan_code, count(*) FROM subscriptions"
                " WHERE status IN ('active','trialing','grace') GROUP BY 1"
            )
        )
        for plan_code, count in plans_rows:
            metrics.ACTIVE_SUBSCRIPTIONS.labels(plan_code).set(count)

    redis: Redis = ctx["redis_app"]
    try:
        metrics.QUEUE_DEPTH.set(await resolve(redis.zcard("arq:queue")))
    except Exception:
        log.debug("metrics.queue_depth_failed", exc_info=True)
    return {"exported": 1}


async def notify_new_tracks(ctx: Ctx) -> dict[str, int]:
    """Every few hours: tell people about new music in the channels they follow."""
    async with session_scope(_maker(ctx)) as session:
        return {"queued": await notifications.queue_new_tracks(session)}


async def notify_weekly_digest(ctx: Ctx) -> dict[str, int]:
    async with session_scope(_maker(ctx)) as session:
        return {"queued": await notifications.queue_weekly_digest(session)}


async def deliver_notifications(ctx: Ctx) -> dict[str, int]:
    """Drains the notification queue. Several workers may run this at once."""
    settings = get_settings()
    maker = _maker(ctx)
    bot = build_bot(settings)
    try:
        return await notifications.deliver(maker, bot, settings)
    finally:
        await bot.session.close()


async def rebuild_wrapped(ctx: Ctx) -> dict[str, int]:
    """Yearly: builds everyone's Wrapped once so opening it is a single row read."""
    maker = _maker(ctx)
    async with session_scope(maker) as session:
        user_ids = await recommendations.active_user_ids(session, days=400)
    built = 0
    for user_id in user_ids:
        async with session_scope(maker) as session:
            payload = await social.wrapped(session, user_id, rebuild=True)
        built += 1 if payload["plays"] else 0
    log.info("wrapped.rebuilt", users=built)
    return {"users": built}


async def probe_metadata(ctx: Ctx) -> dict[str, int]:
    """Backfills album/year/genre from ID3 tags, a small batch at a time.

    Deliberately modest: every probe reads real bytes through the same MTProto
    accounts that serve playback, so this must never look like a crawl.
    """
    settings = get_settings()
    if not settings.edge_internal_url:
        return {"probed": 0, "filled": 0, "failed": 0}
    async with session_scope(_maker(ctx)) as session:
        return await metadata.probe_batch(session, ctx["http"], settings)


# Sized for draining a new catalogue, not for keeping a warm one warm: a resolve is
# one metadata call, no bytes. FloodWait still cools the account and the circuit
# breaker still stops the job, so the ceiling is enforced by Telegram, not by this
# number being cautious.
async def warm_cache(ctx: Ctx) -> dict[str, int]:
    """Puts the opening of popular tracks into the edge cache (ADR-003).

    Reads on the crawling account, so filling the cache never slows down the person
    listening — the mistake that made this job necessary in the first place.
    """
    settings = get_settings()
    async with session_scope(_maker(ctx)) as session:
        return await warming.warm_batch(session, ctx["redis_app"], ctx["http"], settings)


async def enrich_artists(ctx: Ctx) -> dict[str, int]:
    """Gives artists a photo, a few at a time (0014).

    Does nothing at all without Spotify credentials, which is why it is safe to have
    on by default: an installation that never configures them never notices it.
    """
    settings = get_settings()
    async with session_scope(_maker(ctx)) as session:
        return await artistinfo.enrich_batch(session, ctx["http"], settings)


PREWARM_BATCH = 120


async def prewarm_resolver(ctx: Ctx) -> dict[str, int]:
    """Resolves tracks before a user is the one waiting (ADR-002 §C).

    Not a background nicety: with no CDN fallback, this job is what keeps users off
    the inline resolve path. It goes after demand first, then what is liked, played,
    in a playlist, or sitting in a featured channel — and stops the moment the circuit
    breaker says the resolver account is unhappy.
    """
    settings = get_settings()
    if not settings.edge_internal_url:
        return {"resolved": 0, "failed": 0}
    async with session_scope(_maker(ctx)) as session:
        return await resolving.prewarm(session, ctx["http"], settings, limit=PREWARM_BATCH)


async def crawler_healthcheck(ctx: Ctx) -> dict[str, int]:
    """Daily: is the parser still extracting, and is the resolver alive? (ADR-002 §2)

    The failure this exists for is silent by nature — Telegram changes its preview
    markup, pages keep parsing, and the catalogue simply stops growing. Prometheus
    has the same rule, but an operator who is not looking at Grafana still has to be
    told, so the admins get a direct message.
    """
    maker = _maker(ctx)
    async with session_scope(maker) as session:
        parser = await crawling.parser_health(session)
        crawl = await crawling.health(session)
        resolver = await resolving.stats(session)
        admins = list(await session.scalars(text("SELECT tg_id FROM admin_users WHERE is_active")))

    problems: list[str] = []
    if parser["alert"]:
        problems.append(
            f"⚠️ نرخ استخراج پارسر افت کرده: امروز {parser['today_rate']:.0%}"
            f" در برابر {parser['expected_rate']:.0%} روزهای قبل."
            " احتمالاً مارک‌آپ پیش‌نمایش تلگرام عوض شده (RUNBOOK → پارسر شکسته)."
        )
    if crawl["errored"]:
        problems.append(f"🕸 {crawl['errored']} کانال در وضعیت خطای کرال است.")
    if resolver["breaker_open"]:
        problems.append(
            f"🔌 resolver خاموش است ({resolver['consecutive_failures']} خطای پشت‌سرهم)."
            " پخش ترک‌های resolve‌شده سالم است (RUNBOOK → resolver مرده)."
        )
    if not problems:
        log.info("crawler.health_ok", rate=parser["today_rate"], due=crawl["due"])
        return {"alerts": 0, "notified": 0}

    body = "\n\n".join(problems)
    log.warning("crawler.health_alert", problems=len(problems))
    if not admins:
        return {"alerts": len(problems), "notified": 0}
    bot = build_bot(get_settings())
    notified = 0
    try:
        for tg_id in admins:
            notified += 1 if await _notify(bot, int(tg_id), body) else 0
    finally:
        await bot.session.close()
    return {"alerts": len(problems), "notified": notified}
