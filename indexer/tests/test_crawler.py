"""The crawler: pagination, resumability, politeness and backoff.

Nothing here touches the network. Pages are served by a mock transport built from the
real fixtures, so the pagination logic is tested against the same markup Telegram
actually returns.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from tmusic_common.indexer_contract import AudioItem
from tmusic_indexer.webpreview.crawler import (
    CrawlBlocked,
    Crawler,
    CrawlSettings,
    PreviewClient,
    adaptive_interval,
)
from tmusic_indexer.webpreview.parser import PreviewUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "preview"
FAST = CrawlSettings(min_delay_s=0.0, max_delay_s=0.0, backoff_base_s=0.0, backoff_max_s=0.0)


def page(message_ids: list[int], *, audio: list[int] | None = None, username: str = "ch") -> str:
    """A minimal page in Telegram's real shape, so pagination can be driven precisely."""
    audio = audio if audio is not None else message_ids
    blocks = []
    for message_id in message_ids:
        document = (
            f'<a class="tgme_widget_message_document_wrap" href="https://t.me/{username}/{message_id}">'
            '<div class="tgme_widget_message_document_icon accent_bg audio"></div>'
            '<div class="tgme_widget_message_document">'
            f'<div class="tgme_widget_message_document_title">Track {message_id}</div>'
            '<div class="tgme_widget_message_document_extra">Someone</div>'
            "</div></a>"
            if message_id in audio
            else ""
        )
        blocks.append(
            '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
            f'<div class="tgme_widget_message js-widget_message" data-post="{username}/{message_id}">'
            f"{document}"
            '<div class="tgme_widget_message_footer"><span class="tgme_widget_message_views">10</span>'
            f'<a class="tgme_widget_message_date" href="https://t.me/{username}/{message_id}">'
            '<time datetime="2026-09-18T10:00:00+00:00"></time></a></div>'
            "</div></div>"
        )
    header = (
        '<div class="tgme_channel_info">'
        f'<div class="tgme_channel_info_header_title">Channel</div>'
        f'<div class="tgme_channel_info_header_username">@{username}</div>'
        "</div>"
    )
    return f"<html><body>{header}{''.join(blocks)}</body></html>"


class FakeTelegram:
    """Serves 20-message pages walking backwards, like the real preview does."""

    def __init__(self, newest: int = 100, oldest: int = 1, page_size: int = 20) -> None:
        self.newest, self.oldest, self.page_size = newest, oldest, page_size
        self.requests: list[int | None] = []
        self.fail_next: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        before = request.url.params.get("before")
        self.requests.append(int(before) if before else None)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))

        top = int(before) - 1 if before else self.newest
        ids = [i for i in range(max(top - self.page_size + 1, self.oldest), top + 1)]
        return httpx.Response(200, text=page(ids))

    def client(self) -> PreviewClient:
        transport = httpx.MockTransport(self.handler)
        return PreviewClient(FAST, httpx.AsyncClient(transport=transport))


async def collect(crawler: Crawler, **kwargs: Any) -> tuple[list[AudioItem], Any]:
    seen: list[AudioItem] = []

    async def on_batch(items: list[AudioItem], _progress: Any) -> None:
        seen.extend(items)

    progress = await crawler.crawl(1, "ch", on_batch=on_batch, **kwargs)
    return seen, progress


# ── pagination ────────────────────────────────────────────────────────────────


async def test_a_backfill_walks_to_the_beginning_of_the_channel() -> None:
    telegram = FakeTelegram(newest=100, oldest=1)
    crawler = Crawler(telegram.client(), FAST)

    items, progress = await collect(crawler)

    assert progress.finished is True
    assert progress.pages == 5  # 100 messages, 20 per page
    assert len(items) == 100
    assert progress.oldest_seen == 1
    assert progress.newest_seen == 100
    # Each page asked for the one below the previous page's oldest message.
    assert telegram.requests == [None, 81, 61, 41, 21]


async def test_a_crawl_resumes_from_the_stored_cursor() -> None:
    """Phase 2's resumability requirement: kill a job, restart it, no work repeated."""
    telegram = FakeTelegram(newest=100, oldest=1)
    crawler = Crawler(telegram.client(), FAST)

    first_half, progress = await collect(crawler, max_pages=2)
    assert progress.finished is False
    assert progress.oldest_seen == 61
    assert len(first_half) == 40

    # A new run, given only what was persisted.
    resumed = Crawler(telegram.client(), FAST)
    rest, progress2 = await collect(resumed, before=progress.oldest_seen)

    assert progress2.finished is True
    seen_ids = {item.message_id for item in first_half} | {i.message_id for i in rest}
    assert seen_ids == set(range(1, 101))  # everything exactly once
    assert len(first_half) + len(rest) == 100


async def test_an_incremental_crawl_stops_at_the_last_known_message() -> None:
    telegram = FakeTelegram(newest=100, oldest=1)
    crawler = Crawler(telegram.client(), FAST)

    items, progress = await collect(crawler, stop_at=85)

    assert progress.finished is True
    assert progress.pages == 1  # the first page already reached the cursor
    assert [item.message_id for item in items] == list(range(86, 101))


async def test_an_empty_channel_finishes_immediately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page([]))

    client = PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    items, progress = await collect(Crawler(client, FAST))

    assert items == []
    assert progress.finished is True


async def test_channel_metadata_is_taken_from_the_first_page_only() -> None:
    telegram = FakeTelegram(newest=40, oldest=1)
    crawler = Crawler(telegram.client(), FAST)
    _, progress = await collect(crawler)

    assert progress.meta is not None
    assert progress.meta.username == "ch"
    assert progress.meta.title == "Channel"
    assert progress.meta.tg_channel_id is None  # the preview never reveals it


async def test_max_pages_bounds_a_single_run() -> None:
    telegram = FakeTelegram(newest=1000, oldest=1)
    crawler = Crawler(telegram.client(), replace(FAST, max_pages=3))
    _, progress = await collect(crawler)

    assert progress.pages == 3
    assert progress.finished is False  # the scheduler will pick it up again


# ── politeness and failure ────────────────────────────────────────────────────


async def test_requests_are_spaced_out() -> None:
    telegram = FakeTelegram(newest=60, oldest=1)
    polite = CrawlSettings(min_delay_s=0.05, max_delay_s=0.06, backoff_base_s=0.0)
    client = PreviewClient(
        polite, httpx.AsyncClient(transport=httpx.MockTransport(telegram.handler))
    )

    started = asyncio.get_running_loop().time()
    await collect(Crawler(client, polite))
    elapsed = asyncio.get_running_loop().time() - started

    # Three pages means at least two gaps; without pacing this would be ~0.
    assert elapsed >= 0.1


async def test_429_is_retried_with_backoff_then_gives_up() -> None:
    telegram = FakeTelegram(newest=20, oldest=1)
    telegram.fail_next = [429, 429]
    crawler = Crawler(telegram.client(), FAST)

    items, _ = await collect(crawler)
    assert len(items) == 20  # it recovered after the two refusals
    assert len(telegram.requests) == 3

    telegram2 = FakeTelegram(newest=20, oldest=1)
    telegram2.fail_next = [429] * 10
    with pytest.raises(CrawlBlocked) as caught:
        await collect(Crawler(telegram2.client(), FAST))
    assert caught.value.retry_after >= 0


async def test_retry_after_header_is_obeyed() -> None:
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not calls:
            calls.append(0.0)
            return httpx.Response(429, headers={"Retry-After": "0.05"})
        return httpx.Response(200, text=page([1]))

    settings = CrawlSettings(min_delay_s=0.0, max_delay_s=0.0, backoff_base_s=10.0)
    client = PreviewClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    started = asyncio.get_running_loop().time()
    await client.fetch("ch")
    elapsed = asyncio.get_running_loop().time() - started

    # It waited the header's 0.05s, not the 10s it would have chosen on its own.
    assert 0.03 <= elapsed < 1.0


async def test_a_redirect_means_the_channel_has_no_preview() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://t.me/ch"})

    client = PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PreviewUnavailable):
        await client.fetch("ch")


async def test_404_is_not_retried() -> None:
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        return httpx.Response(404)

    client = PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(PreviewUnavailable):
        await client.fetch("gone")
    assert seen == 1


async def test_a_network_error_eventually_becomes_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = PreviewClient(FAST, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(CrawlBlocked):
        await client.fetch("ch")


async def test_the_proxy_pool_rotates() -> None:
    used: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        used.append(request.extensions.get("proxy"))
        return httpx.Response(200, text=page([1]))

    settings = CrawlSettings(min_delay_s=0.0, max_delay_s=0.0, proxies=("http://a:1", "http://b:2"))
    client = PreviewClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for _ in range(4):
        await client.fetch("ch")

    assert used == ["http://a:1", "http://b:2", "http://a:1", "http://b:2"]


# ── scheduling ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("per_day", "expected"),
    [
        (0, 86_400),  # silent channel: look once a day
        (0.5, 86_400),  # a track every other day: still daily
        (20, 86_400),  # one page a day
        (2000, 900),  # very busy: the fifteen-minute floor
    ],
)
def test_adaptive_interval(per_day: float, expected: int) -> None:
    assert adaptive_interval(per_day) == expected


def test_adaptive_interval_scales_between_the_bounds() -> None:
    busy = adaptive_interval(500)
    quiet = adaptive_interval(50)
    assert 900 <= busy < quiet <= 86_400
