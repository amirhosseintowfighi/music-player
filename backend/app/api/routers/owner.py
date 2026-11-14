"""Channel owners: claim a channel you administer and see how it does in the app."""

from __future__ import annotations

from aiogram import Bot
from fastapi import APIRouter, Request

from app.api.deps import Claims, SessionDep, WritableClaims
from app.errors import Unavailable
from app.models import Channel
from app.schemas import OwnerChannelOut, OwnerClaimIn
from app.services import library, owners

router = APIRouter(prefix="/v1/owner", tags=["owner"])


async def _out(session: SessionDep, channel_id: int, lang: str) -> OwnerChannelOut:
    channel = await session.get(Channel, channel_id)
    assert channel is not None
    s = await owners.stats(session, channel)
    return OwnerChannelOut(
        id=channel.id,
        username=channel.username,
        title=channel.title,
        tracks_count=channel.tracks_count,
        app_followers=s.app_followers,
        plays_7d=s.plays_7d,
        listeners_7d=s.listeners_7d,
        plays_30d=s.plays_30d,
        listeners_30d=s.listeners_30d,
        minutes_30d=s.minutes_30d,
        daily=s.daily,
        top_tracks=await library.hydrate_tracks(
            session, s.top_track_ids, "en" if lang == "en" else "fa"
        ),
    )


@router.get("/channels", response_model=list[OwnerChannelOut])
async def my_channels(claims: Claims, session: SessionDep) -> list[OwnerChannelOut]:
    """Every channel this person is a verified administrator of, with its numbers."""
    return [
        await _out(session, channel.id, claims.lang)
        for channel in await owners.owned(session, claims.user_id)
    ]


@router.post("/channels", response_model=OwnerChannelOut)
async def claim_channel(
    body: OwnerClaimIn, request: Request, claims: WritableClaims, session: SessionDep
) -> OwnerChannelOut:
    """Proves ownership through Telegram: the bot asks the channel who its admins are.

    That only works once the bot is in the channel, which is why the error for "the
    bot cannot see it" is its own code: the client then explains how to add the bot.
    """
    bot: Bot | None = getattr(request.app.state, "bot", None)
    if bot is None:
        raise Unavailable("the bot is not running", reason="no_bot")
    channel = await owners.claim(session, bot, claims.user_id, claims.tg_id, body.channel)
    await session.flush()
    return await _out(session, channel.id, claims.lang)
