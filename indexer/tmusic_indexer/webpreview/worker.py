"""The crawl loop: claim channels from the core, walk their pages, post what we find.

Deliberately the same shape as the MTProto worker it replaces — claim, work, report —
so the operational model (leases, retries, "one channel one worker") stays familiar.
What is gone is the account pool: this worker has nothing to get banned.

A page is posted the moment it is parsed rather than at the end of a channel. That is
what makes a killed job cheap: the core already has the cursor for everything posted.
"""

from __future__ import annotations

import asyncio
import contextlib

from tmusic_common.indexer_contract import (
    AudioItem,
    CandidateStatsIn,
    CrawlBatchIn,
    CrawlClaimIn,
    CrawlFailureIn,
    CrawlTaskOut,
    ReleaseIn,
)
from tmusic_common.logging import get_logger
from tmusic_indexer.account import ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.core_client import CoreClient, CoreUnavailable
from tmusic_indexer.mtcrawl import ChannelUnreadable, MtprotoCrawler
from tmusic_indexer.webpreview.crawler import (
    CrawlBlocked,
    Crawler,
    CrawlProgress,
    CrawlSettings,
    PreviewClient,
)
from tmusic_indexer.webpreview.parser import PreviewUnavailable, parse_page

log = get_logger(__name__)


class CrawlWorker:
    def __init__(
        self, settings: Settings, core: CoreClient, account: ResolverAccount | None = None
    ) -> None:
        self.settings = settings
        self.core = core
        self.crawl_settings = CrawlSettings(
            min_delay_s=settings.crawl_min_delay_s,
            max_delay_s=settings.crawl_max_delay_s,
            max_pages=settings.crawl_max_pages,
            proxies=settings.proxy_pool,
        )
        self.client = PreviewClient(self.crawl_settings)
        self.crawler = Crawler(self.client, self.crawl_settings)
        # Only built when a crawling session exists; a task for a channel with no
        # preview is reported unreadable otherwise, never silently dropped.
        self.mtproto = MtprotoCrawler(account, settings) if account is not None else None
        # Set while Telegram is refusing this IP; nothing is claimed until it passes.
        self._blocked_until = 0.0

    async def aclose(self) -> None:
        await self.client.aclose()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except CoreUnavailable as exc:
                log.warning("crawl.core_unavailable", error=str(exc))
            except Exception:
                log.exception("crawl.tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.claim_interval_s)

    async def tick(self) -> None:
        loop = asyncio.get_running_loop()
        if loop.time() < self._blocked_until:
            return
        claim = CrawlClaimIn(
            worker_id=self.settings.worker_id, limit=self.settings.crawl_claim_limit
        )
        tasks = await self.core.crawl_claim(claim)
        for task in tasks:
            await self.run_task(task)
        await self.probe_candidates(claim)

    async def probe_candidates(self, claim: CrawlClaimIn) -> None:
        """Measures suggested channels so the admin queue can be sorted (ADR-002 §3).

        One page each, same politeness as a real crawl. A candidate whose preview is
        off is reported as such instead of scoring zero for no stated reason.
        """
        if asyncio.get_running_loop().time() < self._blocked_until:
            return
        for task in await self.core.candidate_claim(claim):
            try:
                page = parse_page(await self.client.fetch(task.username))
            except PreviewUnavailable:
                await self.core.candidate_stats(
                    task.candidate_id, CandidateStatsIn(unavailable=True)
                )
                continue
            except CrawlBlocked as exc:
                self._blocked_until = asyncio.get_running_loop().time() + exc.retry_after
                return
            await self.core.candidate_stats(
                task.candidate_id,
                CandidateStatsIn(
                    title=page.channel.title,
                    subscribers=page.channel.subscribers,
                    messages=page.stats.messages,
                    audio=page.stats.audio,
                    newest_msg_id=page.newest_id,
                    posts_per_day=page.posts_per_day,
                ),
            )

    async def run_task(self, task: CrawlTaskOut) -> None:
        """One claimed channel. Every exit path reports back, so no lease is orphaned."""
        posted_meta = False

        async def on_batch(items: list[AudioItem], progress: CrawlProgress) -> None:
            nonlocal posted_meta
            result = await self.core.crawl_batch(
                CrawlBatchIn(
                    channel_id=task.channel_id,
                    lease_token=task.lease_token,
                    meta=None if posted_meta else progress.meta,
                    items=items,
                    oldest_msg_id=progress.oldest_seen,
                    newest_msg_id=progress.newest_seen,
                    finished=False,
                    extraction_rate=progress.extraction_rate,
                    page_messages=progress.page_messages,
                    mentions=sorted(progress.mentions)[:200],
                )
            )
            posted_meta = True
            if result is not None and not result.lease_valid:
                raise LeaseLost

        reader = self.crawler if task.source == "web_preview" else self.mtproto
        if reader is None:
            await self.core.crawl_failure(
                task.channel_id,
                CrawlFailureIn(
                    lease_token=task.lease_token,
                    reason="no_crawl_account",
                    detail="mtproto fallback is on but this edge has no crawl* session",
                ),
            )
            return

        try:
            progress = await reader.crawl(
                task.channel_id,
                task.username,
                before=task.before,
                stop_at=task.stop_at,
                on_batch=on_batch,
            )
        except LeaseLost:
            log.info("crawl.lease_lost", channel_id=task.channel_id)
            return
        except PreviewUnavailable as exc:
            # The one failure the brief insists must never be silent.
            await self.core.crawl_failure(
                task.channel_id,
                CrawlFailureIn(
                    lease_token=task.lease_token,
                    reason="preview_disabled",
                    detail=str(exc),
                    preview_disabled=True,
                ),
            )
            return
        except ChannelUnreadable as exc:
            # The account cannot see it either: private, restricted, or deleted. There
            # is no third source, so it stops here with a stated reason rather than
            # bouncing between two readers that both cannot help.
            await self.core.crawl_failure(
                task.channel_id,
                CrawlFailureIn(
                    lease_token=task.lease_token,
                    reason="mtproto_unreadable",
                    detail=str(exc),
                ),
            )
            return
        except CrawlBlocked as exc:
            # Our IP, not this channel. Stop claiming for a while and give it back.
            self._blocked_until = asyncio.get_running_loop().time() + exc.retry_after
            log.warning("crawl.blocked", retry_after=exc.retry_after)
            await self.core.crawl_release(
                task.channel_id,
                ReleaseIn(lease_token=task.lease_token, retry_after_s=int(exc.retry_after)),
            )
            return
        except Exception as exc:
            await self.core.crawl_failure(
                task.channel_id,
                CrawlFailureIn(
                    lease_token=task.lease_token,
                    reason="crawl_error",
                    detail=f"{type(exc).__name__}: {exc}",
                ),
            )
            raise

        # A final, empty batch carries the closing cursors and the "done" flag.
        await self.core.crawl_batch(
            CrawlBatchIn(
                channel_id=task.channel_id,
                lease_token=task.lease_token,
                meta=None if posted_meta else progress.meta,
                items=[],
                oldest_msg_id=progress.oldest_seen,
                newest_msg_id=progress.newest_seen,
                finished=progress.finished,
                extraction_rate=progress.extraction_rate,
                mentions=sorted(progress.mentions)[:200],
            )
        )
        log.info(
            "crawl.channel_done",
            channel_id=task.channel_id,
            username=task.username,
            pages=progress.pages,
            items=progress.items,
            finished=progress.finished,
        )


class LeaseLost(Exception):
    """Another worker took this channel; stop immediately and drop what is in flight."""
