"""arq worker entrypoint: ``arq app.workers.main.WorkerSettings``."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.config import get_settings
from app.db import make_engine, make_sessionmaker
from app.services.meili import MeiliClient
from app.workers import jobs
from tmusic_common.logging import configure_logging, get_logger

settings = get_settings()
configure_logging("worker", settings.log_level, settings.log_json)
log = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    engine = make_engine(settings)
    http = httpx.AsyncClient(timeout=10.0)
    ctx.update(
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        http=http,
        redis_app=Redis.from_url(settings.redis_url, decode_responses=True),
        meili=MeiliClient(
            http,
            settings.meili_url,
            settings.meili_api_key.get_secret_value(),
            settings.meili_index,
        ),
    )
    try:
        await jobs.ensure_search_index(ctx)
    except Exception:
        log.warning("worker.search_setup_failed", exc_info=True)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["redis_app"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    functions: ClassVar[list[Any]] = [
        jobs.reindex_search,
        jobs.subscriptions_daily,
        jobs.recommendations_nightly,
        jobs.generate_mixes,
        jobs.run_broadcasts,
        jobs.export_business_metrics,
        jobs.notify_new_tracks,
        jobs.notify_weekly_digest,
        jobs.deliver_notifications,
        jobs.rebuild_wrapped,
        jobs.probe_metadata,
        jobs.prewarm_resolver,
        jobs.crawler_healthcheck,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(jobs.sync_search, second={0, 15, 30, 45}, run_at_startup=True, unique=True),
        cron(jobs.refresh_track_counters, minute={0, 15, 30, 45}, unique=True),
        cron(jobs.export_business_metrics, second={30}, unique=True),
        cron(jobs.edge_healthcheck, second={5}, unique=True),
        # Picks up scheduled broadcasts; the send loop paces itself.
        cron(jobs.run_broadcasts, minute=set(range(0, 60, 2)), unique=True),
        cron(jobs.maintenance_daily, hour={3}, minute={10}, unique=True),
        # Notifications: produce a few times a day, deliver every minute.
        cron(jobs.notify_new_tracks, hour={6, 12, 16}, minute={0}, unique=True),
        cron(jobs.notify_weekly_digest, weekday=4, hour={12}, minute={0}, unique=True),
        cron(jobs.deliver_notifications, minute=set(range(60)), unique=True),
        # 09:00 Tehran (UTC+3:30) — a renewal reminder at 3am helps nobody.
        cron(jobs.subscriptions_daily, hour={5}, minute={30}, unique=True),
        # The matrix first, then the per-user mixes that read it.
        cron(jobs.recommendations_nightly, hour={1}, minute={0}, unique=True),
        # ID3 backfill: hourly and small, so it never competes with playback.
        cron(jobs.probe_metadata, minute={20}, unique=True),
        # Pre-warm is on the critical path now (ADR-002, 2026-09-20): every ten
        # minutes, so a track somebody asked for is ready before they ask again.
        cron(jobs.prewarm_resolver, minute={3, 13, 23, 33, 43, 53}, unique=True),
        # Daily, after a full day of crawling has been recorded.
        cron(jobs.crawler_healthcheck, hour={7}, minute={0}, unique=True),
        # Wrapped is rebuilt in the first days of January.
        cron(jobs.rebuild_wrapped, month={1}, day={2}, hour={4}, unique=True),
        cron(jobs.generate_mixes, hour={2}, minute={0}, unique=True),
    ]
    max_jobs = 20
    job_timeout = 600
