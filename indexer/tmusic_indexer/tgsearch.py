"""Asking Telegram where the music is, instead of waiting to be told.

Mention-based discovery only ever finds channels that other channels already link
to, so a catalogue grows outward from wherever it happened to start. Telegram's own
search does not have that shape:

- ``messages.searchGlobal`` with the music filter returns public posts that *are*
  audio files — every result names the channel that posted it, which is a channel
  worth crawling by definition;
- ``contacts.search`` returns channels whose name or username matches, which finds
  the obvious ones the first kind misses.

Both only produce usernames. Nothing here decides what to crawl: the names go into
the same candidate queue as everything else, get probed and scored, and an admin
still approves them. A search result is a lead, not a decision.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import Counter
from telethon import functions, types
from telethon.errors import FloodWaitError, RPCError

from tmusic_common.logging import get_logger
from tmusic_indexer.account import ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.webpreview.crawler import CrawlBlocked

log = get_logger(__name__)

FOUND = Counter("telegram_search_channels_total", "Channels returned by Telegram search")
SEARCHES = Counter("telegram_search_queries_total", "Telegram searches run", ["kind"])

GLOBAL_LIMIT = 100
CONTACTS_LIMIT = 50


def usernames_of(chats: Any) -> set[str]:
    """Public channel usernames in a search result, ignoring groups and users."""
    found: set[str] = set()
    for chat in chats or []:
        if not isinstance(chat, types.Channel) or getattr(chat, "megagroup", False):
            continue
        name = getattr(chat, "username", None)
        if name:
            found.add(str(name).lower())
    return found


class TelegramSearch:
    def __init__(self, account: ResolverAccount, settings: Settings) -> None:
        self.account = account
        self.settings = settings

    async def run(self, term: str) -> set[str]:
        """Both searches for one term. FloodWait is raised, never slept through."""
        account = await self.account.ready_or_reload()
        if account is None:
            return set()

        found: set[str] = set()
        for kind, request in (
            (
                "global",
                functions.messages.SearchGlobalRequest(
                    q=term,
                    filter=types.InputMessagesFilterMusic(),
                    min_date=None,
                    max_date=None,
                    offset_rate=0,
                    offset_peer=types.InputPeerEmpty(),
                    offset_id=0,
                    limit=GLOBAL_LIMIT,
                ),
            ),
            ("contacts", functions.contacts.SearchRequest(q=term, limit=CONTACTS_LIMIT)),
        ):
            try:
                result = await account.client(request)
            except FloodWaitError as exc:
                self.account.on_flood(int(exc.seconds))
                raise CrawlBlocked(float(exc.seconds)) from exc
            except RPCError as exc:
                # A rejected query is not a broken account: log it and try the next.
                log.info("tgsearch.rejected", term=term, kind=kind, error=str(exc))
                continue
            SEARCHES.labels(kind).inc()
            found |= usernames_of(getattr(result, "chats", None))

        FOUND.inc(len(found))
        log.info("tgsearch.done", term=term, channels=len(found))
        return found
