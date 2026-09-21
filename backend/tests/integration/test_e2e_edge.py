"""End to end after ADR-002: web preview → core → user API → resolve → stream.

Only Telegram is faked — the preview pages and one MTProto session. Everything in
between is the real code from both services talking real HTTP:

    t.me/s/<channel>  →  CrawlWorker  →  /internal/indexer/crawl/*  →  catalogue
    user plays a track  →  lazy resolve over /internal/resolve  →  bytes from the edge

This is the test that would catch "the pieces work but do not fit together".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, EdgeNode, Track
from app.services import plans
from tests.conftest import bearer, login

pytest.importorskip("tmusic_indexer")

from telethon import types
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings as EdgeSettings
from tmusic_indexer.core_client import CoreClient
from tmusic_indexer.stream import Sources, create_app
from tmusic_indexer.webpreview.crawler import CrawlSettings, PreviewClient
from tmusic_indexer.webpreview.worker import CrawlWorker

AUDIO = bytes(range(256)) * 8_000
SONGS = {
    1: ("Shabe Barooni", "Moein"),
    2: ("Pol", "Googoosh"),
    3: ("Pol 🎵", "Googoosh"),  # the same song re-posted with a decorated title
}
CHANNEL = "realmusic"


def message_block(message_id: int) -> str:
    title, performer = SONGS[message_id]
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message js-widget_message" data-post="{CHANNEL}/{message_id}">'
        f'<a class="tgme_widget_message_document_wrap" href="https://t.me/{CHANNEL}/{message_id}">'
        '<div class="tgme_widget_message_document_icon accent_bg audio"></div>'
        '<div class="tgme_widget_message_document">'
        f'<div class="tgme_widget_message_document_title">{title}</div>'
        f'<div class="tgme_widget_message_document_extra">{performer}</div>'
        "</div></a>"
        # Telegram renders an @mention as a link, which is how discovery sees it.
        '<div class="tgme_widget_message_text">از کانال '
        '<a href="https://t.me/friend_channel">@friend_channel</a> هم ببینید</div>'
        '<div class="tgme_widget_message_footer">'
        '<span class="tgme_widget_message_views">1.2K</span>'
        f'<a class="tgme_widget_message_date" href="https://t.me/{CHANNEL}/{message_id}">'
        f'<time datetime="2026-09-0{message_id}T10:00:00+00:00"></time></a>'
        "</div></div></div>"
    )


def preview_page(ids: list[int]) -> str:
    header = (
        '<div class="tgme_channel_info">'
        '<div class="tgme_channel_info_header_title">Real Music</div>'
        f'<div class="tgme_channel_info_header_username">@{CHANNEL}</div>'
        '<div class="tgme_channel_info_counter"><span class="counter_value">12.3K</span>'
        '<span class="counter_type">subscribers</span></div>'
        '<div class="tgme_channel_info_description">Persian hits</div>'
        "</div>"
    )
    return f"<html><body>{header}{''.join(message_block(i) for i in ids)}</body></html>"


def telegram_preview(request: httpx.Request) -> httpx.Response:
    """The public preview: the whole channel on the first page, then nothing.

    Also answers for the channel this one mentions, which is what the candidate
    probe fetches to score it.
    """
    if request.url.path.lower() != f"/s/{CHANNEL}":
        return httpx.Response(200, text=preview_page([1]))
    before = request.url.params.get("before")
    ids = [i for i in sorted(SONGS, reverse=True) if not before or i < int(before)]
    return httpx.Response(200, text=preview_page(sorted(ids, reverse=True)))


def mtproto_message(message_id: int) -> Any:
    title, performer = SONGS[message_id]
    # Messages 2 and 3 are literally the same file, which only a resolve can reveal.
    doc_id = 2002 if message_id in (2, 3) else 2000 + message_id
    document = types.Document(
        id=doc_id, access_hash=1, file_reference=b"r", date=datetime(2026, 1, 1, tzinfo=UTC),
        mime_type="audio/mpeg", size=len(AUDIO), dc_id=2, thumbs=None,
        attributes=[types.DocumentAttributeAudio(duration=180, title=title, performer=performer)],
    )  # fmt: skip
    return SimpleNamespace(
        id=message_id,
        media=types.MessageMediaDocument(document=document),
        date=datetime(2026, 9, message_id, tzinfo=UTC),
        views=1200,
        message="",
    )


class Telegram:
    """The single resolver session: it can read one message and download bytes."""

    def __init__(self) -> None:
        self.reads: list[int] = []

    async def get_input_entity(self, username: str) -> Any:
        assert username.lower() == CHANNEL
        return types.InputPeerChannel(channel_id=31337, access_hash=7)

    async def get_messages(self, peer: Any, ids: int | None = None, **_: Any) -> Any:
        self.reads.append(int(ids or 0))
        return mtproto_message(int(ids or 0))

    async def iter_download(self, document: Any, offset: int, request_size: int, chunk_size: int,
                            limit: int, file_size: int) -> AsyncIterator[bytes]:  # fmt: skip
        for i in range(limit):
            start = offset + i * chunk_size
            if start >= len(AUDIO):
                return
            yield AUDIO[start : start + chunk_size]


async def test_a_channel_goes_from_preview_page_to_playing_bytes(
    client: httpx.AsyncClient, session: AsyncSession, tmp_path: Path, settings: Any
) -> None:
    # 0. The crawler owns the catalogue on this deployment.
    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    session.add(EdgeNode(host="http://edge.test"))
    await session.commit()
    plans.clear_caches()

    # 1. A user adds a public channel.
    auth = bearer(await login(client, 4242))
    added = await client.post("/v1/library/channels", json={"ref": "t.me/RealMusic"}, headers=auth)
    channel_id = added.json()["channel"]["id"]
    await session.execute(
        text(
            "UPDATE channels SET source_type = 'web_preview', crawl_status = 'idle',"
            " next_crawl_at = now() WHERE id = :i"
        ).bindparams(i=channel_id)
    )
    await session.commit()

    edge_settings = EdgeSettings(
        internal_api_token="internal-token", tg_api_id=1, tg_api_hash="h",
        session_enc_key="a-very-long-test-key", bot_token="123456:TEST",
        stream_signing_keys=settings.stream_signing_keys.get_secret_value(),
        sessions_dir=tmp_path / "s", crawl_min_delay_s=0.0, crawl_max_delay_s=0.0,
    )  # fmt: skip

    # 2. The edge crawls it through the internal API — no Telegram account involved.
    core = CoreClient(client, "http://test", "internal-token")
    worker = CrawlWorker(edge_settings, core)
    worker.client = PreviewClient(
        CrawlSettings(min_delay_s=0.0, max_delay_s=0.0),
        httpx.AsyncClient(transport=httpx.MockTransport(telegram_preview)),
    )
    worker.crawler.client = worker.client
    await worker.tick()
    await worker.client.aclose()

    status = (await client.get(f"/v1/channels/{channel_id}", headers=auth)).json()
    assert status["status"] == "active"
    assert status["progress_pct"] == 100
    assert status["title"] == "Real Music"

    tracks = (await client.get("/v1/library/tracks", headers=auth)).json()["items"]
    # The re-post folded into one track by the existing dedup, untouched by ADR-002.
    assert [t["title"] for t in tracks] == ["Pol", "Shabe Barooni"]
    assert tracks[1]["artists"][0]["name"] == "معین"

    rows = list(await session.scalars(select(Track)))
    assert {row.resolve_status for row in rows} == {"unresolved"}
    assert all(row.file_unique_id is None for row in rows)

    # 3. The channel mentioned another one; that is a candidate, never an index job.
    candidates = (
        await session.execute(
            text("SELECT username, status, score, audio_ratio FROM channel_candidates")
        )
    ).all()
    assert [(c.username, c.status) for c in candidates] == [("friend_channel", "pending")]
    # The same tick probed it: a candidate arrives already scored, not blank.
    assert candidates[0].audio_ratio == 1.0
    assert candidates[0].score > 0
    assert (
        await session.scalar(select(Channel.id).where(Channel.username == "friend_channel")) is None
    )

    # 4. The user plays a track: it is resolved on demand, then streamed.
    telegram = Telegram()
    resolver = ResolverAccount(edge_settings)
    resolver.account = Account(key="acc1", client=telegram, id=1)
    edge_app = create_app(edge_settings, Sources(edge_settings, resolver, client))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=edge_app), base_url="http://edge.test"
    ) as edge:
        state = client.app.state.app  # type: ignore[attr-defined]
        state.http = edge
        state.settings = settings.model_copy(update={"edge_internal_url": "http://edge.test"})

        ticket = (await client.post(f"/v1/tracks/{tracks[1]['id']}/stream", headers=auth)).json()
        assert ticket["size"] == len(AUDIO)
        assert ticket["url"].startswith("http://edge.test/s/")
        assert telegram.reads == [1]  # the resolve: one message, once

        # A second ticket for the same track costs no resolve at all.
        await client.post(f"/v1/tracks/{tracks[1]['id']}/stream", headers=auth)
        assert telegram.reads == [1]

        played = await edge.get(ticket["url"], headers={"Range": "bytes=1048570-1048600"})
        assert played.status_code == 206
        assert played.content == AUDIO[1048570:1048601]

    session.expire_all()
    resolved = (await session.scalars(select(Track).where(Track.id == tracks[1]["id"]))).one()
    assert resolved.resolve_status == "resolved"
    assert (resolved.duration, resolved.file_size) == (180, len(AUDIO))

    await session.execute(
        text("UPDATE feature_flags SET value = '\"mtproto\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()


async def test_the_crawler_needs_no_telegram_account_at_all(
    client: httpx.AsyncClient, session: AsyncSession, tmp_path: Path, settings: Any
) -> None:
    """The point of ADR-002: an edge with zero sessions still builds the catalogue."""
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
        sessions_dir=tmp_path / "empty",
    )  # fmt: skip
    resolver = ResolverAccount(edge_settings)
    await resolver.start()
    assert resolver.account is None  # no session file anywhere

    worker = CrawlWorker(edge_settings, CoreClient(client, "http://test", "internal-token"))
    worker.client = PreviewClient(
        CrawlSettings(min_delay_s=0.0, max_delay_s=0.0),
        httpx.AsyncClient(transport=httpx.MockTransport(telegram_preview)),
    )
    worker.crawler.client = worker.client
    await asyncio.wait_for(worker.tick(), timeout=10)
    await worker.client.aclose()

    assert await session.scalar(text("SELECT count(*) FROM tracks")) == 3

    await session.execute(
        text("UPDATE feature_flags SET value = '\"mtproto\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()
