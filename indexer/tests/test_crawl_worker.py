"""The crawl worker: every exit path has to report back, or a lease is orphaned."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from tests.test_crawler import FAST, FakeTelegram, page
from tmusic_common.indexer_contract import (
    CandidateStatsIn,
    CandidateTaskOut,
    CrawlBatchOut,
    CrawlTaskOut,
)
from tmusic_indexer.config import Settings
from tmusic_indexer.webpreview.crawler import PreviewClient
from tmusic_indexer.webpreview.worker import CrawlWorker


class FakeCore:
    def __init__(self, lease_valid: bool = True) -> None:
        self.claims = 0
        self.batches: list[Any] = []
        self.releases: list[Any] = []
        self.failures: list[Any] = []
        self.tasks: list[CrawlTaskOut] = []
        self.candidates: list[CandidateTaskOut] = []
        self.stats: list[tuple[int, CandidateStatsIn]] = []
        self.lease_valid = lease_valid

    async def crawl_claim(self, _body: Any) -> list[CrawlTaskOut]:
        self.claims += 1
        tasks, self.tasks = self.tasks, []
        return tasks

    async def crawl_batch(self, batch: Any) -> CrawlBatchOut:
        self.batches.append(batch)
        return CrawlBatchOut(lease_valid=self.lease_valid)

    async def candidate_claim(self, _body: Any) -> list[CandidateTaskOut]:
        tasks, self.candidates = self.candidates, []
        return tasks

    async def candidate_stats(self, candidate_id: int, body: CandidateStatsIn) -> None:
        self.stats.append((candidate_id, body))

    async def crawl_release(self, channel_id: int, body: Any) -> None:
        self.releases.append((channel_id, body))

    async def crawl_failure(self, channel_id: int, body: Any) -> None:
        self.failures.append((channel_id, body))


def worker(core: FakeCore, client: Any) -> CrawlWorker:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        internal_api_token="t",
        api_id=1,
        api_hash="h",
        stream_signing_keys="k" * 32,
    )
    w = CrawlWorker(settings, core)  # type: ignore[arg-type]
    w.crawl_settings = FAST
    w.client = client
    w.crawler.client = client
    w.crawler.settings = FAST
    return w


def task(**extra: Any) -> CrawlTaskOut:
    return CrawlTaskOut(
        channel_id=7,
        username="ch",
        lease_token="tok",
        mode="backfill",
        needs_meta=True,
        **extra,
    )


async def test_a_channel_is_crawled_and_closed_with_a_final_batch() -> None:
    telegram = FakeTelegram(newest=40, oldest=1)
    core = FakeCore()
    w = worker(core, telegram.client())

    await w.run_task(task())

    assert len(core.batches) == 3  # two pages, then the closing batch
    assert core.batches[0].meta is not None
    assert core.batches[1].meta is None  # metadata is posted once
    assert sum(len(b.items) for b in core.batches) == 40
    last = core.batches[-1]
    assert (last.items, last.finished, last.oldest_msg_id) == ([], True, 1)


async def test_a_lost_lease_stops_the_run_immediately() -> None:
    telegram = FakeTelegram(newest=100, oldest=1)
    core = FakeCore(lease_valid=False)
    w = worker(core, telegram.client())

    await w.run_task(task())

    assert len(core.batches) == 1  # it gave up after the first refusal
    assert core.failures == []


async def test_a_channel_without_a_preview_is_reported_not_swallowed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://t.me/ch"})

    core = FakeCore()
    w = worker(core, PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    await w.run_task(task())

    (channel_id, body) = core.failures[0]
    assert channel_id == 7
    assert (body.reason, body.preview_disabled) == ("preview_disabled", True)
    assert core.batches == []


async def test_a_blocked_ip_releases_the_channel_and_pauses_claiming() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    core = FakeCore()
    w = worker(core, PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    core.tasks = [task()]

    await w.tick()

    assert core.releases and core.releases[0][0] == 7
    assert core.failures == []
    # The next tick claims nothing: the problem is our IP, not that channel.
    w._blocked_until = asyncio.get_running_loop().time() + 60
    await w.tick()
    assert core.claims == 1


async def test_an_unexpected_error_is_reported_before_it_propagates() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page([1]))

    core = FakeCore()
    w = worker(core, PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler))))

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("parser exploded")

    w.crawler.crawl = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await w.run_task(task())

    assert core.failures[0][1].reason == "crawl_error"
    assert "parser exploded" in core.failures[0][1].detail


async def test_exceptions_never_kill_the_loop() -> None:
    core = FakeCore()
    w = worker(core, FakeTelegram().client())
    stop = asyncio.Event()

    async def explode(_body: Any) -> list[CrawlTaskOut]:
        core.claims += 1
        stop.set()
        raise RuntimeError("core on fire")

    core.crawl_claim = explode  # type: ignore[method-assign]
    await asyncio.wait_for(w.run(stop), timeout=5)
    assert core.claims == 1


async def test_a_candidate_is_measured_from_one_page() -> None:
    telegram = FakeTelegram(newest=20, oldest=1)
    core = FakeCore()
    w = worker(core, telegram.client())
    core.candidates = [CandidateTaskOut(candidate_id=3, username="suggested")]

    await w.tick()

    (candidate_id, stats) = core.stats[0]
    assert candidate_id == 3
    assert (stats.messages, stats.audio) == (20, 20)
    assert stats.newest_msg_id == 20
    assert stats.unavailable is False
    assert len(telegram.requests) == 1  # one page, nothing more


async def test_a_candidate_without_a_preview_is_reported_as_such() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://t.me/x"})

    core = FakeCore()
    w = worker(core, PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    core.candidates = [CandidateTaskOut(candidate_id=4, username="private")]

    await w.tick()

    assert core.stats[0][1].unavailable is True


async def test_candidates_are_not_probed_while_the_ip_is_blocked() -> None:
    core = FakeCore()
    w = worker(core, FakeTelegram().client())
    core.candidates = [CandidateTaskOut(candidate_id=5, username="later")]
    w._blocked_until = asyncio.get_running_loop().time() + 60

    await w.tick()

    assert core.stats == []
