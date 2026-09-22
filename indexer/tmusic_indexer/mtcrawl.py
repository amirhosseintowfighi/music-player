"""Reading a channel over MTProto, for the ones with no public web preview.

The web crawler is the default and will stay the default: it needs no account and
has nothing to get banned. But the preview is a per-channel setting, and plenty of
music channels have it off — for those the web path can never work, no matter how
politely it asks.

This reads the same channel the way a person scrolling it does, and produces exactly
what the web crawler produces: ``AudioItem``s and a ``CrawlProgress``. Everything
downstream — batching, leases, cursors, dedup, the parser health metric — is the same
code, because the difference between the two sources ends here.

Two rules this module exists to keep:

- **A separate account from the resolver.** The resolver is what makes playback work;
  if the crawling account is limited, playback must not go with it. The split is by
  session name (``crawl*``), so an operator gets it by logging a second one in.
- **FloodWait is never slept through.** It cools the account and gives the lease back,
  exactly like the web crawler does when Telegram blocks the IP.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter
from telethon import types
from telethon.errors import FloodWaitError, RPCError

from tmusic_common.indexer_contract import AudioItem, ChannelMeta
from tmusic_common.logging import get_logger
from tmusic_indexer.account import ResolverAccount
from tmusic_indexer.config import Settings
from tmusic_indexer.file_ids import document_unique_id
from tmusic_indexer.webpreview.crawler import CrawlBlocked, CrawlProgress

log = get_logger(__name__)

MESSAGES = Counter("mtproto_crawl_messages_total", "Messages read over MTProto")
ITEMS = Counter("mtproto_crawl_items_total", "Audio items found over MTProto")

# One "page", so progress is reported as often as the web crawler reports it.
PAGE = 100
_MENTION = re.compile(r"(?:https?://)?t\.me/(?!s/|joinchat|\+)([A-Za-z][A-Za-z0-9_]{3,31})")
_AT_MENTION = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")


class ChannelUnreadable(Exception):
    """The account cannot see this channel: private, restricted, or gone."""


def audio_of(message: Any) -> tuple[Any, Any] | None:
    """``(document, audio attribute)`` for a music message, else None."""
    document = getattr(getattr(message, "media", None), "document", None)
    if document is None:
        return None
    for attribute in getattr(document, "attributes", []) or []:
        if isinstance(attribute, types.DocumentAttributeAudio) and not getattr(
            attribute, "voice", False
        ):
            return document, attribute
    return None


def file_name_of(document: Any) -> str | None:
    for attribute in getattr(document, "attributes", []) or []:
        if isinstance(attribute, types.DocumentAttributeFilename):
            return str(attribute.file_name)[:512]
    return None


def item_of(message: Any) -> AudioItem | None:
    """One message → one track, or None when it is not music."""
    found = audio_of(message)
    if found is None:
        return None
    document, audio = found
    posted = message.date or datetime.now(UTC)
    caption = getattr(message, "message", None) or None
    return AudioItem(
        message_id=int(message.id),
        posted_at=posted if posted.tzinfo else posted.replace(tzinfo=UTC),
        views=getattr(message, "views", None),
        # Unlike the web preview, MTProto gives the document id — so the dedup key is
        # known immediately instead of on first play.
        file_unique_id=document_unique_id(int(document.id)),
        duration=max(int(getattr(audio, "duration", 0) or 0), 0),
        file_size=max(int(getattr(document, "size", 0) or 0), 0),
        mime_type=(getattr(document, "mime_type", None) or None),
        title=(getattr(audio, "title", None) or None),
        performer=(getattr(audio, "performer", None) or None),
        file_name=file_name_of(document),
        caption=caption[:4096] if caption else None,
        has_thumb=bool(getattr(document, "thumbs", None)),
    )


def mentions_of(message: Any) -> set[str]:
    text = getattr(message, "message", None) or ""
    found = {m.group(1).lower() for m in _MENTION.finditer(text)}
    found |= {m.group(1).lower() for m in _AT_MENTION.finditer(text)}
    return found


def meta_of(entity: Any, username: str) -> ChannelMeta:
    return ChannelMeta(
        tg_channel_id=int(entity.id) if getattr(entity, "id", None) else None,
        username=getattr(entity, "username", None) or username,
        title=str(getattr(entity, "title", None) or username),
        subscribers=getattr(entity, "participants_count", None),
    )


class MtprotoCrawler:
    """Same interface as the web ``Crawler``, so the worker only picks one."""

    def __init__(self, account: ResolverAccount, settings: Settings) -> None:
        self.account = account
        self.settings = settings

    async def _pause(self) -> None:
        await asyncio.sleep(
            random.uniform(  # noqa: S311 — jitter, not cryptography
                self.settings.crawl_min_delay_s, self.settings.crawl_max_delay_s
            )
        )

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
        """Walks backwards from ``before``, oldest-ward, like the web crawler does."""
        account = self.account.account
        if account is None or not account.available():
            raise ChannelUnreadable("no crawling account is available")

        progress = CrawlProgress(channel_id=channel_id, username=username)
        limit = max_pages if max_pages is not None else self.settings.max_crawl_pages_mtproto

        try:
            peer = await self.account.resolve(username, None)
            entity = await account.client.get_entity(peer)
        except FloodWaitError as exc:
            self.account.on_flood(int(exc.seconds))
            raise CrawlBlocked(float(exc.seconds)) from exc
        except (RPCError, ValueError, TypeError) as exc:
            raise ChannelUnreadable(f"{username}: {type(exc).__name__}: {exc}") from exc

        progress.meta = meta_of(entity, username)
        cursor = before or 0

        for _ in range(limit):
            messages: list[Any] = []
            try:
                async for message in account.client.iter_messages(
                    peer, limit=PAGE, offset_id=cursor, min_id=stop_at or 0
                ):
                    messages.append(message)
            except FloodWaitError as exc:
                self.account.on_flood(int(exc.seconds))
                raise CrawlBlocked(float(exc.seconds)) from exc
            except RPCError as exc:
                raise ChannelUnreadable(f"{username}: {type(exc).__name__}: {exc}") from exc

            if not messages:
                progress.finished = True
                break

            # ``min_id`` already bounds this server-side; the filter is here because an
            # incremental run must never re-post what the core already has, whatever
            # the server decides to include.
            items = [
                item
                for item in (item_of(m) for m in messages)
                if item is not None and (stop_at is None or item.message_id > stop_at)
            ]
            ids = [int(m.id) for m in messages]
            progress.pages += 1
            progress.messages += len(messages)
            progress.items += len(items)
            progress.page_messages = len(messages)
            progress.page_items = len(items)
            for message in messages:
                progress.mentions |= mentions_of(message)
            progress.mentions.discard(username.lower())
            MESSAGES.inc(len(messages))
            ITEMS.inc(len(items))

            oldest, newest = min(ids), max(ids)
            progress.oldest_seen = (
                oldest if progress.oldest_seen is None else min(progress.oldest_seen, oldest)
            )
            progress.newest_seen = (
                newest if progress.newest_seen is None else max(progress.newest_seen, newest)
            )

            if on_batch is not None:
                await on_batch(items, progress)

            if oldest <= 1:
                progress.finished = True
                break
            cursor = oldest  # get_messages is exclusive of offset_id
            await self._pause()

        log.info(
            "mtcrawl.done",
            channel_id=channel_id,
            username=username,
            pages=progress.pages,
            items=progress.items,
            finished=progress.finished,
        )
        return progress
