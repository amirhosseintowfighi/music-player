"""Music people send to the bot, indexed like music found in a channel.

Two reasons this path is worth more than its size suggests:

- **It needs no MTProto account.** A file the bot received carries a Bot API file id,
  so it is playable the moment it arrives — no resolve, no waiting, and it keeps
  working on a deployment that never logs an account in at all.
- **It scales with users, not with crawling.** Every listener who forwards a track
  they like is one more indexer, and the ones they send are the ones people want.

A forwarded post is credited to the channel it came from, which is also how those
channels get discovered. A file with no origin lands in one shared uploads channel:
its entries are keyed by the file itself, so the same song sent by a thousand people
is one row, not a thousand.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel
from app.services.ingest import IngestStats, ingest_items
from tmusic_common.indexer_contract import AudioItem
from tmusic_common.logging import get_logger

log = get_logger(__name__)

UPLOADS_TITLE = "Uploads"
# Every channel row needs a username or a Telegram id. This one is not a real
# channel, so it gets an id Telegram can never issue: they are all positive.
UPLOADS_TG_ID = -1


def upload_message_id(file_unique_id: str) -> int:
    """A stable id for a file in the uploads channel.

    ``channel_tracks`` is keyed by (channel, message id), and a message id is only
    unique inside one chat — two users can easily send their tenth message each. So
    the file's own identity becomes the key: the same song from a thousand people is
    one entry, and no upload can ever overwrite somebody else's.
    """
    return int.from_bytes(hashlib.blake2b(file_unique_id.encode(), digest_size=7).digest(), "big")


async def uploads_channel(session: AsyncSession) -> Channel:
    """The shared home for files that arrived without a channel behind them.

    Deliberately has no username: the crawler only claims channels that have one, so
    this row can never be queued for crawling.
    """
    channel = (
        await session.scalars(select(Channel).where(Channel.tg_channel_id == UPLOADS_TG_ID))
    ).one_or_none()
    if channel is None:
        channel = Channel(
            tg_channel_id=UPLOADS_TG_ID,
            title=UPLOADS_TITLE,
            source="bot_admin",
            source_type="bot_member",
            status="active",
            is_public=False,
            progress_pct=100,
        )
        session.add(channel)
        await session.flush()
    return channel


async def ingest_upload(
    session: AsyncSession, channel: Channel, item: AudioItem, *, bot_id: int
) -> IngestStats:
    stats = await ingest_items(session, channel, [item], bot_id=bot_id)
    log.info(
        "upload.ingested",
        channel_id=channel.id,
        inserted=stats.inserted,
        duplicates=stats.duplicates,
    )
    return stats
