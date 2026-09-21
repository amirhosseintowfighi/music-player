"""ADR-002 §8 load test: back-fill a 20,000-message channel and report the cost.

Not part of the normal suite — it writes ~20k rows and takes minutes. Run it with:

    LOADTEST=1 pytest tests/load -s -q

Telegram is replaced by a local generator of real-shaped preview pages with zero
network delay, so what this measures is *our* cost per page: parsing, HTTP, ingest,
normalisation, dedup and cursor bookkeeping. Real crawls are slower by exactly the
politeness delay (1.5–4 s per page), which the report states rather than simulates.
"""

from __future__ import annotations

import os
import time
import tracemalloc
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import plans

pytestmark = pytest.mark.skipif(
    os.environ.get("LOADTEST") != "1", reason="set LOADTEST=1 to run the crawl load test"
)

pytest.importorskip("tmusic_indexer")

from tmusic_indexer.config import Settings as EdgeSettings  # noqa: E402
from tmusic_indexer.core_client import CoreClient  # noqa: E402
from tmusic_indexer.webpreview.crawler import CrawlSettings, PreviewClient  # noqa: E402
from tmusic_indexer.webpreview.worker import CrawlWorker  # noqa: E402

CHANNEL = "loadtest_channel"
MESSAGES = 20_000
PAGE = 20
# A realistic mix: most posts are music, some are text, a few repeat an earlier track.
ARTISTS = ["Moein", "Googoosh", "Dariush", "Hayedeh", "Shadmehr Aghili", "Ebi"]


def block(message_id: int) -> str:
    artist = ARTISTS[message_id % len(ARTISTS)]
    title = f"Track {message_id // 3}" if message_id % 97 == 0 else f"Track {message_id}"
    document = (
        f'<a class="tgme_widget_message_document_wrap" href="https://t.me/{CHANNEL}/{message_id}">'
        '<div class="tgme_widget_message_document_icon accent_bg audio"></div>'
        '<div class="tgme_widget_message_document">'
        f'<div class="tgme_widget_message_document_title">{artist} - {title}</div>'
        f'<div class="tgme_widget_message_document_extra">{artist}</div>'
        "</div></a>"
        if message_id % 10 != 7  # one in ten posts is not music
        else '<div class="tgme_widget_message_text">فقط یک متن</div>'
    )
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message js-widget_message" data-post="{CHANNEL}/{message_id}">'
        f"{document}"
        '<div class="tgme_widget_message_footer">'
        '<span class="tgme_widget_message_views">1.2K</span>'
        f'<a class="tgme_widget_message_date" href="https://t.me/{CHANNEL}/{message_id}">'
        '<time datetime="2026-09-18T10:00:00+00:00"></time></a>'
        "</div></div></div>"
    )


def page(request: httpx.Request) -> httpx.Response:
    before = request.url.params.get("before")
    top = int(before) - 1 if before else MESSAGES
    ids = range(max(top - PAGE + 1, 1), top + 1)
    header = (
        '<div class="tgme_channel_info">'
        '<div class="tgme_channel_info_header_title">Load Test</div>'
        f'<div class="tgme_channel_info_header_username">@{CHANNEL}</div></div>'
    )
    body = "".join(block(i) for i in sorted(ids, reverse=True))
    return httpx.Response(200, text=f"<html><body>{header}{body}</body></html>")


async def test_backfill_twenty_thousand_messages(
    client: httpx.AsyncClient, session: AsyncSession, tmp_path: Any, settings: Any
) -> None:
    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    await session.execute(
        text(
            "INSERT INTO channels (username, source_type, status, crawl_status, next_crawl_at)"
            " VALUES (:u, 'web_preview', 'pending', 'idle', now())"
        ).bindparams(u=CHANNEL)
    )
    await session.commit()
    plans.clear_caches()

    edge_settings = EdgeSettings(
        internal_api_token="internal-token", tg_api_id=1, tg_api_hash="h",
        session_enc_key="a-very-long-test-key", bot_token="123456:TEST",
        stream_signing_keys=settings.stream_signing_keys.get_secret_value(),
        sessions_dir=tmp_path / "s", crawl_max_pages=MESSAGES // PAGE + 10,
    )  # fmt: skip
    worker = CrawlWorker(edge_settings, CoreClient(client, "http://test", "internal-token"))
    worker.client = PreviewClient(
        CrawlSettings(min_delay_s=0.0, max_delay_s=0.0, max_pages=MESSAGES // PAGE + 10),
        httpx.AsyncClient(transport=httpx.MockTransport(page)),
    )
    worker.crawler.client = worker.client
    worker.crawler.settings = worker.client.settings

    tracemalloc.start()
    started = time.perf_counter()
    await worker.tick()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    await worker.client.aclose()

    row = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM tracks) AS tracks,"
                " (SELECT count(*) FROM tracks WHERE canonical_track_id IS NOT NULL) AS dupes,"
                " (SELECT count(*) FROM channel_tracks) AS links,"
                " (SELECT progress_pct FROM channels WHERE username = :u) AS pct,"
                " (SELECT status FROM channels WHERE username = :u) AS status,"
                " (SELECT oldest_crawled_msg_id FROM channels WHERE username = :u) AS oldest"
            ).bindparams(u=CHANNEL)
        )
    ).one()
    pages = MESSAGES // PAGE

    print(
        "\n== ADR-002 section 8: crawl load test ======================\n"
        f"messages ............. {MESSAGES:,} over {pages:,} preview pages\n"
        f"wall clock ........... {elapsed:.1f}s  ({elapsed / pages * 1000:.0f} ms/page)\n"
        f"throughput ........... {MESSAGES / elapsed:,.0f} messages/s\n"
        f"peak python heap ..... {peak / 1e6:.1f} MB\n"
        f"tracks ............... {row.tracks:,} ({row.dupes:,} folded as duplicates)\n"
        f"channel_tracks ....... {row.links:,}\n"
        f"with real politeness . ~{pages * 2.75 / 3600:.1f} h at 1.5-4 s between pages\n"
        "==========================================================="
    )

    assert row.status == "active"
    assert row.pct == 100
    assert row.oldest == 1  # walked to the first message of the channel
    assert row.links == MESSAGES - MESSAGES // 10  # every music post is linked once

    await session.execute(
        text("UPDATE feature_flags SET value = '\"mtproto\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()
