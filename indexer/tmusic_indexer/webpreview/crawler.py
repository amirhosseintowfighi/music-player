"""Walks a channel's public preview pages and hands batches to the core.

Shape of the work (ADR-002):

- ``https://t.me/s/<username>`` is the newest page; ``?before=<id>`` walks backwards
  twenty messages at a time until a page comes back empty.
- Every channel keeps two cursors, so a job that dies mid-backfill resumes from where
  it stopped rather than starting over.
- The only thing Telegram can rate-limit here is an IP, which is cheap to replace —
  unlike an account. Even so: randomised delay between requests, a proxy pool taken
  from the environment, and exponential backoff on 429/5xx.

The crawler is deliberately dumb about music. It parses, packages and posts; the
normalisation, artist parsing and dedup all still happen in the core, unchanged.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from prometheus_client import Counter, Gauge, Histogram

from tmusic_common.indexer_contract import AudioItem, ChannelMeta
from tmusic_common.logging import get_logger
from tmusic_indexer.webpreview.parser import (
    PageResult,
    PreviewUnavailable,
    describe,
    parse_page,
)

log = get_logger(__name__)

PAGES = Counter("crawl_pages_total", "Preview pages fetched", ["result"])
ITEMS = Counter("crawl_items_total", "Audio items extracted")
EXTRACTION = Histogram(
    "crawl_extraction_rate",
    "Share of messages on a page that yielded an audio item",
    buckets=(0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0),
)
PAGE_LATENCY = Histogram("crawl_page_seconds", "Time to fetch one preview page")
ACTIVE = Gauge("crawl_active_channels", "Channels being crawled right now")

BASE_URL = "https://t.me/s/{username}"
# Telegram renders twenty messages per preview page; asking for more does nothing.
PAGE_SIZE = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class CrawlBlocked(Exception):
    """Telegram is refusing this IP for now; the caller should back off, not retry."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"blocked, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


@dataclass(slots=True)
class CrawlSettings:
    """Everything tunable about politeness, so none of it is hidden in the code."""

    min_delay_s: float = 1.5
    max_delay_s: float = 4.0
    timeout_s: float = 20.0
    max_pages: int = 500  # one job never walks a whole channel in a single run
    max_retries: int = 4
    backoff_base_s: float = 5.0
    backoff_max_s: float = 300.0
    proxies: tuple[str, ...] = ()


@dataclass(slots=True)
class CrawlProgress:
    """What the core needs to resume exactly where this run stopped."""

    channel_id: int
    username: str
    oldest_seen: int | None = None
    newest_seen: int | None = None
    pages: int = 0
    items: int = 0
    messages: int = 0
    # The page just parsed, which is what the parser health monitor watches.
    page_messages: int = 0
    page_items: int = 0
    finished: bool = False
    meta: ChannelMeta | None = None
    mentions: set[str] = field(default_factory=set)

    @property
    def extraction_rate(self) -> float:
        return self.items / self.messages if self.messages else 0.0


class PreviewClient:
    """HTTP with the manners the brief asks for: jitter, proxy rotation, backoff."""

    def __init__(self, settings: CrawlSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,fa;q=0.9"},
            timeout=settings.timeout_s,
        )
        self._proxies = itertools.cycle(settings.proxies) if settings.proxies else None
        self._last_request = 0.0

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def _sleep_between_requests(self) -> None:
        """A random gap, measured from the previous request rather than a fixed sleep."""
        delay = random.uniform(  # noqa: S311 - politeness jitter, not cryptography
            self.settings.min_delay_s, self.settings.max_delay_s
        )
        waited = time.monotonic() - self._last_request
        if waited < delay:
            await asyncio.sleep(delay - waited)

    async def fetch(self, username: str, before: int | None = None) -> str:
        url = BASE_URL.format(username=username)
        params = {"before": str(before)} if before else None
        attempt = 0
        while True:
            await self._sleep_between_requests()
            proxy = next(self._proxies) if self._proxies else None
            started = time.monotonic()
            try:
                response = await self._client.get(
                    url, params=params, extensions={"proxy": proxy} if proxy else {}
                )
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt > self.settings.max_retries:
                    PAGES.labels("network_error").inc()
                    raise CrawlBlocked(self.settings.backoff_max_s) from exc
                await self._backoff(attempt)
                continue
            finally:
                self._last_request = time.monotonic()
                PAGE_LATENCY.observe(time.monotonic() - started)

            if response.status_code == 200:
                PAGES.labels("ok").inc()
                return response.text
            if response.status_code in (301, 302, 303, 307, 308):
                # t.me redirects to the plain channel page when there is no preview.
                PAGES.labels("no_preview").inc()
                raise PreviewUnavailable(f"{username}: redirected, no public preview")
            if response.status_code == 404:
                PAGES.labels("not_found").inc()
                raise PreviewUnavailable(f"{username}: not found")
            if response.status_code == 429 or response.status_code >= 500:
                attempt += 1
                retry_after = _retry_after(response) or self._backoff_delay(attempt)
                if attempt > self.settings.max_retries:
                    PAGES.labels("blocked").inc()
                    raise CrawlBlocked(retry_after)
                log.warning(
                    "crawl.backoff",
                    username=username,
                    status=response.status_code,
                    attempt=attempt,
                    sleep=round(retry_after, 1),
                )
                await asyncio.sleep(retry_after)
                continue
            PAGES.labels(f"http_{response.status_code}").inc()
            raise PreviewUnavailable(f"{username}: HTTP {response.status_code}")

    def _backoff_delay(self, attempt: int) -> float:
        grown = self.settings.backoff_base_s * (2 ** (attempt - 1))
        return float(min(grown, self.settings.backoff_max_s))

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._backoff_delay(attempt))


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def page_meta(result: PageResult, fallback_username: str) -> ChannelMeta | None:
    """Channel metadata as the core's ``_apply_meta`` expects it."""
    info = result.channel
    if not (info.title or info.username):
        return None
    return ChannelMeta(
        tg_channel_id=None,
        username=info.username or fallback_username,
        title=(info.title or info.username or fallback_username)[:256],
        description=info.description,
        music_total=None,
        subscribers=info.subscribers,
    )


class Crawler:
    """One channel at a time; the scheduler decides which and how often."""

    def __init__(self, client: PreviewClient, settings: CrawlSettings) -> None:
        self.client = client
        self.settings = settings

    async def crawl(
        self,
        channel_id: int,
        username: str,
        *,
        before: int | None = None,
        stop_at: int | None = None,
        max_pages: int | None = None,
        on_batch: Any = None,
    ) -> CrawlProgress:
        """Walks backwards from ``before`` until the channel ends or a limit is hit.

        ``stop_at`` is the incremental cursor: stop as soon as a page contains only
        messages we already have. ``on_batch`` is awaited with each page's items so a
        long backfill streams into the core instead of buffering in memory.
        """
        progress = CrawlProgress(channel_id=channel_id, username=username)
        limit = max_pages if max_pages is not None else self.settings.max_pages
        cursor = before
        ACTIVE.inc()
        try:
            for _ in range(limit):
                html = await self.client.fetch(username, before=cursor)
                result = parse_page(html)
                progress.pages += 1
                progress.messages += result.stats.messages
                progress.items += len(result.items)
                progress.page_messages = result.stats.messages
                progress.page_items = len(result.items)
                progress.mentions.update(result.mentions)
                ITEMS.inc(len(result.items))
                EXTRACTION.observe(result.stats.extraction_rate)

                if progress.meta is None:
                    progress.meta = page_meta(result, username)

                if not result.message_ids:
                    progress.finished = True
                    break

                oldest, newest = result.oldest_id, result.newest_id
                progress.oldest_seen = (
                    oldest
                    if progress.oldest_seen is None
                    else min(progress.oldest_seen, oldest or progress.oldest_seen)
                )
                progress.newest_seen = (
                    newest
                    if progress.newest_seen is None
                    else max(progress.newest_seen, newest or progress.newest_seen)
                )

                fresh = [
                    item for item in result.items if stop_at is None or item.message_id > stop_at
                ]
                if on_batch is not None:
                    # Posted even when the page held no music: it still moves the cursor
                    # (so a silent stretch is not re-crawled) and still counts towards
                    # the extraction rate the parser monitor watches.
                    await on_batch(fresh, progress)

                log.info("crawl.page", username=username, cursor=cursor, **describe(result))

                if stop_at is not None and oldest is not None and oldest <= stop_at:
                    progress.finished = True  # caught up with what we already had
                    break
                if oldest is None or oldest <= 1:
                    progress.finished = True  # reached the first message of the channel
                    break
                cursor = oldest
            return progress
        finally:
            ACTIVE.dec()


def adaptive_interval(items_per_day: float, floor_s: int = 900, ceiling_s: int = 86_400) -> int:
    """How long until this channel is worth reading again.

    A channel that posts twenty tracks a day earns the fifteen-minute floor; one that
    posts monthly gets a daily look. Between them the interval is simply "how long
    until roughly one page of new messages should exist".
    """
    if items_per_day <= 0:
        return ceiling_s
    seconds_per_page = PAGE_SIZE * 86_400 / items_per_day
    return int(min(max(seconds_per_page, floor_s), ceiling_s))


def summarise(items: Sequence[AudioItem]) -> dict[str, Any]:
    return {
        "items": len(items),
        "first": items[0].message_id if items else None,
        "last": items[-1].message_id if items else None,
    }
