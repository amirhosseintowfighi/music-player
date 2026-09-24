"""The MTProto fallback: same output as the web crawler, different reader.

What these pin down is the part that is easy to get wrong when two sources feed one
pipeline: the items must be indistinguishable downstream, the cursors must move
backwards the same way, and a limited account must give the lease back instead of
quietly producing nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from telethon.errors import ChannelPrivateError, FloodWaitError

from tests.conftest import FakeClient, audio_doc, audio_message
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.file_ids import document_unique_id
from tmusic_indexer.mtcrawl import ChannelUnreadable, MtprotoCrawler, item_of, mentions_of
from tmusic_indexer.webpreview.crawler import CrawlBlocked


def account_with(settings: Settings, client: FakeClient) -> ResolverAccount:
    account = ResolverAccount(settings, role="crawler")
    account.account = Account(key="crawl1", client=client)
    return account


class SearchingFailure(FakeClient):
    """A client whose peer resolution fails the way Telethon fails on a dead name."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def get_input_entity(self, username: str) -> Any:
        raise self.error


def crawler(settings: Settings, client: FakeClient) -> MtprotoCrawler:
    settings.crawl_min_delay_s = 0.0
    settings.crawl_max_delay_s = 0.0
    return MtprotoCrawler(account_with(settings, client), settings)


def test_a_music_message_becomes_the_same_item_the_web_crawler_posts() -> None:
    item = item_of(audio_message(5, audio_doc(4242, title="Gole Sangam", performer="Hayedeh")))
    assert item is not None
    assert item.message_id == 5
    assert item.title == "Gole Sangam"
    assert item.performer == "Hayedeh"
    assert item.duration == 200
    assert item.mime_type == "audio/mpeg"
    assert item.file_name == "song.mp3"
    assert item.has_thumb is True
    # Unlike the preview, MTProto knows the dedup key straight away.
    assert item.file_unique_id == document_unique_id(4242)


def test_voice_notes_and_non_media_messages_are_not_tracks() -> None:
    assert item_of(audio_message(1, audio_doc(10, voice=True))) is None
    assert item_of(type("M", (), {"id": 2, "media": None})()) is None


def test_mentions_come_from_the_message_text() -> None:
    message = audio_message(1, text="از کانال @Another_Chan و https://t.me/third_one")
    assert mentions_of(message) == {"another_chan", "third_one"}


async def test_it_walks_backwards_and_streams_every_page(settings: Settings) -> None:
    client = FakeClient([audio_message(i, audio_doc(i * 10)) for i in range(1, 251)])
    batches: list[tuple[int, int | None]] = []

    async def on_batch(items: list[Any], progress: Any) -> None:
        batches.append((len(items), progress.oldest_seen))

    progress = await crawler(settings, client).crawl(7, "music", on_batch=on_batch)

    assert progress.items == 250
    assert progress.finished is True
    assert progress.oldest_seen == 1
    assert progress.newest_seen == 250
    assert len(batches) == 3  # 100 + 100 + 50
    assert [count for count, _ in batches] == [100, 100, 50]
    # Every page moved the cursor strictly older, which is what makes a resume exact.
    assert [oldest for _, oldest in batches] == [151, 51, 1]


async def test_an_incremental_run_stops_at_the_cursor(settings: Settings) -> None:
    client = FakeClient([audio_message(i, audio_doc(i * 10)) for i in range(1, 21)])
    progress = await crawler(settings, client).crawl(7, "music", stop_at=15)
    assert progress.items == 5
    assert progress.oldest_seen == 16


async def test_a_flood_wait_cools_the_account_and_gives_the_lease_back(
    settings: Settings,
) -> None:
    client = FakeClient([audio_message(1)])
    client.fail_with["iter"] = FloodWaitError(request=None)
    mt = crawler(settings, client)

    with pytest.raises(CrawlBlocked):
        await mt.crawl(7, "music")
    assert mt.account.account is not None
    assert mt.account.account.status in ("cooling", "limited")


async def test_a_channel_the_account_cannot_see_is_reported_not_retried(
    settings: Settings,
) -> None:
    client = FakeClient([audio_message(1)])
    client.fail_with["iter"] = ChannelPrivateError(request=None)
    with pytest.raises(ChannelUnreadable):
        await crawler(settings, client).crawl(7, "music")


async def test_without_a_session_nothing_pretends_to_crawl(settings: Settings) -> None:
    mt = MtprotoCrawler(ResolverAccount(settings, role="crawler"), settings)
    with pytest.raises(ChannelUnreadable, match="login crawl1"):
        await mt.crawl(7, "music")


async def test_a_session_logged_in_after_startup_is_picked_up(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session is always created after the edge is already running."""
    from tmusic_indexer.crypto import save_session

    account = ResolverAccount(settings, client_factory=lambda _s: FakeClient(), role="crawler")
    await account.start()
    mt = MtprotoCrawler(account, settings)
    with pytest.raises(ChannelUnreadable, match="login crawl1"):
        await mt.crawl(7, "music")

    save_session(settings.sessions_dir, "crawl1", "s", settings.session_enc_key.get_secret_value())
    monkeypatch.setattr("tmusic_indexer.account.RELOAD_INTERVAL_S", 0.0)
    progress = await mt.crawl(7, "music")
    assert progress.finished is True


async def test_a_cooling_account_says_so_instead_of_no_account(settings: Settings) -> None:
    import time as _time

    from tmusic_indexer.account import Account

    account = ResolverAccount(settings, role="crawler")
    account.account = Account(key="crawl1", client=FakeClient(), status="cooling")
    account.account.cooling_until = _time.time() + 300
    with pytest.raises(ChannelUnreadable, match="cooling"):
        await MtprotoCrawler(account, settings).crawl(7, "music")


async def test_a_username_nobody_owns_is_reported_as_permanent(settings: Settings) -> None:
    """Mention discovery turns up bots, people and typos; none of them become channels."""
    client = SearchingFailure(ValueError('No user has "typo_name" as username'))
    with pytest.raises(ChannelUnreadable) as caught:
        await crawler(settings, client).crawl(7, "typo_name")
    assert caught.value.permanent is True


async def test_a_private_channel_is_not_permanent(settings: Settings) -> None:
    """It could be opened tomorrow, so it keeps its place in the retry schedule."""
    client = FakeClient([audio_message(1)])
    client.fail_with["iter"] = ChannelPrivateError(request=None)
    with pytest.raises(ChannelUnreadable) as caught:
        await crawler(settings, client).crawl(7, "shy")
    assert caught.value.permanent is False
