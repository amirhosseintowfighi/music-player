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
from tmusic_indexer.mtcrawl import MtprotoCrawler
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
        self.terms: list[str] = []
        self.searches: list[tuple[str, list[str]]] = []

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

    async def search_terms(self) -> list[str]:
        return self.terms

    async def search_found(self, term: str, usernames: list[str]) -> int:
        self.searches.append((term, usernames))
        return len(usernames)


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


def task(channel_id: int = 7, **extra: Any) -> CrawlTaskOut:
    return CrawlTaskOut(
        channel_id=channel_id,
        username="ch",
        mode="backfill",
        needs_meta=True,
        **{"lease_token": "tok", **extra},
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


# ── the MTProto fallback ──────────────────────────────────────────────────────


async def test_a_task_for_a_preview_less_channel_uses_the_account(
    settings: Settings,
) -> None:
    """The source on the task, not the worker's mood, decides which reader runs."""
    from tests.conftest import FakeClient, audio_doc, audio_message
    from tests.test_mtcrawl import account_with

    core = FakeCore()
    telegram = FakeTelegram(newest=40, oldest=1)
    w = worker(core, telegram.client())
    tg_client = FakeClient([audio_message(i, audio_doc(i * 10)) for i in range(1, 6)])
    settings.crawl_min_delay_s = settings.crawl_max_delay_s = 0.0
    w.mtproto = MtprotoCrawler(account_with(settings, tg_client), settings)

    await w.run_task(task(source="mtproto"))

    assert core.failures == []
    # Five items, not the forty the web fake holds: the account did the reading.
    assert sum(len(batch.items) for batch in core.batches) == 5


async def test_without_a_crawl_session_the_channel_is_failed_not_dropped() -> None:
    core = FakeCore()
    w = worker(core, FakeTelegram(newest=10, oldest=1).client())
    w.mtproto = None

    await w.run_task(task(source="mtproto"))

    assert core.batches == []
    assert [body.reason for _, body in core.failures] == ["no_crawl_account"]


async def test_the_worker_searches_at_most_once_per_interval(settings: Settings) -> None:
    """The one call nobody is waiting for, so it must stay rare."""
    from tests.test_tgsearch import SearchingClient, channel
    from tests.test_tgsearch import searcher as make_searcher

    core = FakeCore()
    core.terms = ["موزیک", "remix"]
    w = worker(core, FakeTelegram(newest=1, oldest=1).client())
    settings.search_interval_s = 3600.0
    w.settings = settings
    w.search = make_searcher(settings, SearchingClient([channel(1, "found_one")]))

    await w.search_for_channels()
    await w.search_for_channels()  # same interval: nothing more goes out

    assert core.searches == [("موزیک", ["found_one"])]


async def test_search_walks_the_term_list(settings: Settings) -> None:
    from tests.test_tgsearch import SearchingClient, channel
    from tests.test_tgsearch import searcher as make_searcher

    core = FakeCore()
    core.terms = ["one", "two"]
    w = worker(core, FakeTelegram(newest=1, oldest=1).client())
    settings.search_interval_s = 0.0
    w.settings = settings
    w.search = make_searcher(settings, SearchingClient([channel(1, "c")]))

    await w.search_for_channels()
    await w.search_for_channels()
    await w.search_for_channels()

    assert [term for term, _ in core.searches] == ["one", "two", "one"]


async def test_no_terms_means_the_feature_is_off(settings: Settings) -> None:
    from tests.test_tgsearch import SearchingClient, channel
    from tests.test_tgsearch import searcher as make_searcher

    core = FakeCore()
    core.terms = []
    w = worker(core, FakeTelegram(newest=1, oldest=1).client())
    settings.search_interval_s = 0.0
    w.settings = settings
    w.search = make_searcher(settings, SearchingClient([channel(1, "c")]))

    await w.search_for_channels()
    assert core.searches == []


async def test_channels_are_crawled_side_by_side(settings: Settings) -> None:
    """One slow channel used to hold up the whole backlog behind it."""
    core = FakeCore()
    telegram = FakeTelegram(newest=20, oldest=1)
    w = worker(core, telegram.client())
    settings.crawl_parallel = 3
    w.settings = settings
    core.tasks = [task(channel_id=i, lease_token=f"tok{i}") for i in (1, 2, 3)]

    started: list[int] = []
    original = w.run_task

    async def watched(t: Any) -> None:
        started.append(t.channel_id)
        await asyncio.sleep(0)  # let the others start before this one finishes
        await original(t)

    w.run_task = watched  # type: ignore[method-assign]
    await w.tick()

    assert started == [1, 2, 3]
    assert {batch.channel_id for batch in core.batches} == {1, 2, 3}


async def test_one_failing_channel_does_not_stop_the_others(settings: Settings) -> None:
    core = FakeCore()
    w = worker(core, FakeTelegram(newest=20, oldest=1).client())
    settings.crawl_parallel = 3
    w.settings = settings
    core.tasks = [task(channel_id=i, lease_token=f"tok{i}") for i in (1, 2)]

    original = w.run_task

    async def explode(t: Any) -> None:
        if t.channel_id == 1:
            raise RuntimeError("boom")
        await original(t)

    w.run_task = explode  # type: ignore[method-assign]
    await w.tick()  # must not raise

    assert {batch.channel_id for batch in core.batches} == {2}
