from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, UserChannel
from tmusic_common.indexer_contract import AudioItem

_ids = count(1)
BASE_TIME = datetime(2026, 9, 1, tzinfo=UTC)


def item(
    title: str | None,
    performer: str | None = None,
    *,
    msg: int | None = None,
    fuid: str | None = None,
    duration: int = 200,
    **extra: Any,
) -> AudioItem:
    n = next(_ids)
    message_id = msg if msg is not None else n
    return AudioItem(
        message_id=message_id,
        posted_at=extra.pop("posted_at", BASE_TIME + timedelta(minutes=message_id)),
        file_unique_id=fuid or f"AgADtest{n:06d}",
        duration=duration,
        file_size=extra.pop("file_size", 4_000_000),
        mime_type="audio/mpeg",
        title=title,
        performer=performer,
        **extra,
    )


async def make_channel(session: AsyncSession, username: str, **values: Any) -> Channel:
    row = {
        "username": username,
        "title": values.pop("title", username.title()),
        "status": values.pop("status", "active"),
        **values,
    }
    channel = (await session.scalars(insert(Channel).values(row).returning(Channel))).one()
    await session.flush()
    return channel


async def subscribe(session: AsyncSession, user_id: int, *channel_ids: int) -> None:
    await session.execute(
        insert(UserChannel).values([{"user_id": user_id, "channel_id": c} for c in channel_ids])
    )
