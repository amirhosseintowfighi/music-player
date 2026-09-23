"""Discovery by asking Telegram, rather than by waiting for a mention.

What matters here is restraint as much as reach: a search is the one call this
service makes with nobody waiting for it, so it must stay rare, it must never sleep
through a FloodWait, and a query Telegram rejects must not look like a broken
account.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from telethon import types
from telethon.errors import FloodWaitError, SearchQueryEmptyError

from tests.conftest import FakeClient
from tmusic_indexer.account import Account, ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.tgsearch import TelegramSearch, usernames_of
from tmusic_indexer.webpreview.crawler import CrawlBlocked


def channel(channel_id: int, username: str | None, megagroup: bool = False) -> Any:
    return types.Channel(
        id=channel_id,
        title=f"c{channel_id}",
        photo=types.ChatPhotoEmpty(),
        date=None,
        username=username,
        access_hash=1,
        broadcast=not megagroup,
        megagroup=megagroup,
    )


class SearchingClient(FakeClient):
    """A client whose __call__ answers search requests instead of GetFullChannel."""

    def __init__(self, results: list[Any], fail: Exception | None = None) -> None:
        super().__init__()
        self.results = results
        self.fail = fail
        self.requests: list[Any] = []

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(chats=self.results)


def searcher(settings: Settings, client: FakeClient) -> TelegramSearch:
    account = ResolverAccount(settings, role="crawler")
    account.account = Account(key="crawl1", client=client)
    return TelegramSearch(account, settings)


def test_only_public_broadcast_channels_are_leads() -> None:
    chats = [
        channel(1, "music_one"),
        channel(2, None),  # private: nothing to queue
        channel(3, "chat_group", megagroup=True),  # a group, not a channel
        channel(4, "MUSIC_TWO"),
    ]
    assert usernames_of(chats) == {"music_one", "music_two"}
    assert usernames_of(None) == set()


async def test_one_term_asks_both_ways_and_merges_the_answers(settings: Settings) -> None:
    client = SearchingClient([channel(1, "from_search"), channel(2, "also_found")])
    found = await searcher(settings, client).run("موزیک")

    assert found == {"from_search", "also_found"}
    # Posts that are music, and channels whose name matches: two different questions.
    assert len(client.requests) == 2


async def test_a_flood_wait_stops_searching_and_cools_the_account(
    settings: Settings,
) -> None:
    client = SearchingClient([], fail=FloodWaitError(request=None))
    search = searcher(settings, client)

    with pytest.raises(CrawlBlocked):
        await search.run("music")
    assert search.account.account is not None
    assert search.account.account.status in ("cooling", "limited")


async def test_a_rejected_query_is_not_a_broken_account(settings: Settings) -> None:
    """Telegram refuses some queries outright; that is one bad term, not an outage."""
    client = SearchingClient([], fail=SearchQueryEmptyError(request=None))
    assert await searcher(settings, client).run("") == set()


async def test_without_an_account_it_asks_nothing(settings: Settings) -> None:
    search = TelegramSearch(ResolverAccount(settings, role="crawler"), settings)
    assert await search.run("music") == set()
