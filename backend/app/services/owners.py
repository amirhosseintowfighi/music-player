"""Channel owners: how a channel does in the app, for the people who run it (0017).

Ownership is what Telegram says it is: an administrator of the channel, checked by
the bot. Someone who makes the bot an admin is recorded on the spot; anyone else
asks, and the bot looks them up in the channel's member list.

The numbers are aggregates only: plays, distinct listeners and minutes. Never who
listened — an owner learns that their channel is heard, not by whom.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, InvalidInput
from app.models import Channel, ChannelOwner
from app.services import channels
from tmusic_common.logging import get_logger

log = get_logger(__name__)

DAYS = 30
TOP_TRACKS = 5
ADMIN = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def record(session: AsyncSession, channel_id: int, user_id: int) -> None:
    await session.execute(
        insert(ChannelOwner)
        .values(channel_id=channel_id, user_id=user_id)
        .on_conflict_do_update(
            index_elements=[ChannelOwner.channel_id, ChannelOwner.user_id],
            set_={"verified_at": datetime.now(UTC)},
        )
    )


async def claim(session: AsyncSession, bot: Bot, user_id: int, tg_id: int, raw_ref: str) -> Channel:
    """Makes ``user_id`` an owner of the channel if Telegram says they administer it."""
    ref = channels.parse_channel_ref(raw_ref)
    channel, _ = await channels.get_or_create_channel(session, ref, added_by=user_id)
    if channel.tg_channel_id is not None:
        chat_id: int | str = -(10**12) - channel.tg_channel_id
    elif channel.username:
        chat_id = f"@{channel.username}"
    else:
        raise InvalidInput("channel has no address", reason="no_address")
    try:
        member = await bot.get_chat_member(chat_id, tg_id)
    except TelegramAPIError as exc:
        # Telegram only shows a channel's members to a bot that is in it.
        log.info("owners.lookup_failed", channel_id=channel.id, error=str(exc))
        raise InvalidInput("the bot cannot see this channel", reason="bot_not_admin") from exc
    if member.status not in ADMIN:
        raise Forbidden("not an administrator of this channel", reason="not_admin")
    await record(session, channel.id, user_id)
    return channel


@dataclass(frozen=True, slots=True)
class ChannelStats:
    channel: Channel
    app_followers: int
    plays_7d: int
    listeners_7d: int
    plays_30d: int
    listeners_30d: int
    minutes_30d: int
    daily: list[int]
    top_track_ids: list[int]


# Every copy of a song posted here counts: plays are recorded on the canonical track,
# so each query below matches the canonical ids of everything the channel posted.


async def stats(session: AsyncSession, channel: Channel) -> ChannelStats:
    totals = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE played_at > now() - interval '7 days') AS p7,"
                " count(DISTINCT user_id) FILTER (WHERE played_at > now() - interval '7 days')"
                "   AS l7,"
                " count(*) AS p30, count(DISTINCT user_id) AS l30,"
                " COALESCE(sum(duration_played), 0) AS seconds"
                " FROM play_history"
                " WHERE track_id IN (SELECT COALESCE(g.canonical_track_id, g.id)"
                "   FROM channel_tracks ct JOIN tracks g ON g.id = ct.track_id"
                "   WHERE ct.channel_id = :c)"
                "   AND played_at > now() - make_interval(days => :days)"
            ).bindparams(c=channel.id, days=DAYS)
        )
    ).one()
    by_day = {
        row.day: row.plays
        for row in await session.execute(
            text(
                "SELECT (played_at AT TIME ZONE 'UTC')::date AS day, count(*) AS plays"
                " FROM play_history"
                " WHERE track_id IN (SELECT COALESCE(g.canonical_track_id, g.id)"
                "   FROM channel_tracks ct JOIN tracks g ON g.id = ct.track_id"
                "   WHERE ct.channel_id = :c)"
                "   AND played_at > now() - make_interval(days => :days)"
                " GROUP BY 1"
            ).bindparams(c=channel.id, days=DAYS)
        )
    }
    today = datetime.now(UTC).date()
    daily = [by_day.get(today - timedelta(days=n), 0) for n in range(DAYS - 1, -1, -1)]
    top = await session.execute(
        text(
            "SELECT track_id FROM play_history"
            " WHERE track_id IN (SELECT COALESCE(g.canonical_track_id, g.id)"
            "   FROM channel_tracks ct JOIN tracks g ON g.id = ct.track_id"
            "   WHERE ct.channel_id = :c)"
            "   AND played_at > now() - make_interval(days => :days)"
            " GROUP BY track_id ORDER BY count(*) DESC, track_id LIMIT :lim"
        ).bindparams(c=channel.id, days=DAYS, lim=TOP_TRACKS)
    )
    followers = await session.scalar(
        text("SELECT count(*) FROM user_channels WHERE channel_id = :c").bindparams(c=channel.id)
    )
    return ChannelStats(
        channel=channel,
        app_followers=int(followers or 0),
        plays_7d=int(totals.p7),
        listeners_7d=int(totals.l7),
        plays_30d=int(totals.p30),
        listeners_30d=int(totals.l30),
        minutes_30d=round(int(totals.seconds) / 60),
        daily=daily,
        top_track_ids=[row.track_id for row in top],
    )


async def owned(session: AsyncSession, user_id: int) -> list[Channel]:
    return list(
        (
            await session.scalars(
                select(Channel)
                .join(ChannelOwner, ChannelOwner.channel_id == Channel.id)
                .where(ChannelOwner.user_id == user_id, Channel.status != "blacklisted")
                .order_by(Channel.title)
            )
        ).all()
    )
