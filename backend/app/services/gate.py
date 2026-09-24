"""Channels a listener must join before the app opens.

The list lives in ``settings.required_channels`` and is edited from the admin panel,
so adding or dropping a channel is a row change, not a deploy.

Two things this deliberately does not do:

- **It does not ask Telegram on every request.** A membership answer is cached per
  user for a few minutes; the gate exists to grow channels, not to be a real-time
  access control, and a check on the playback path would put a Telegram round trip
  between a tap and a sound.
- **It does not lock anyone out when Telegram will not answer.** The bot has to be an
  administrator of a channel for ``getChatMember`` to work at all. If a name is
  misconfigured or the bot was removed, the gate opens rather than sealing every user
  out of the product — a broken setting must not be an outage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Setting
from tmusic_common.logging import get_logger

log = get_logger(__name__)

CACHE_TTL_S = 300
TIMEOUT_S = 5.0
# Telegram's own words for "this person is in the chat".
MEMBER_STATUSES = frozenset({"creator", "administrator", "member", "restricted"})


@dataclass(frozen=True, slots=True)
class RequiredChannel:
    username: str
    url: str


def _cache_key(tg_id: int) -> str:
    return f"gate:{tg_id}"


async def required(session: AsyncSession) -> list[RequiredChannel]:
    row = await session.get(Setting, "required_channels")
    value = row.value if row is not None else None
    if not isinstance(value, list):
        return []
    out: list[RequiredChannel] = []
    for entry in value[:10]:
        name = str(entry).strip().lstrip("@")
        if name:
            out.append(RequiredChannel(username=name, url=f"https://t.me/{name}"))
    return out


async def _is_member(
    http: httpx.AsyncClient, settings: Settings, username: str, tg_id: int
) -> bool:
    base = settings.tg_api_base.rstrip("/")
    url = f"{base}/bot{settings.bot_token.get_secret_value()}/getChatMember"
    try:
        response = await http.get(
            url, params={"chat_id": f"@{username}", "user_id": tg_id}, timeout=TIMEOUT_S
        )
    except httpx.HTTPError as exc:
        log.warning("gate.unreachable", channel=username, error=str(exc))
        return True  # never lock the product behind our own outage
    body = response.json() if response.content else {}
    if not body.get("ok"):
        # "chat not found" / "bot is not a member": a misconfigured gate opens.
        log.warning("gate.check_failed", channel=username, error=body.get("description"))
        return True
    status = str(body.get("result", {}).get("status", ""))
    return status in MEMBER_STATUSES


async def missing(
    session: AsyncSession,
    redis: Redis,
    http: httpx.AsyncClient,
    settings: Settings,
    tg_id: int,
) -> list[RequiredChannel]:
    """Which required channels this user has not joined. Empty list = let them in."""
    channels = await required(session)
    if not channels:
        return []

    cached = await redis.get(_cache_key(tg_id))
    if cached is not None:
        names = set(json.loads(cached))
        return [c for c in channels if c.username in names]

    absent = [c for c in channels if not await _is_member(http, settings, c.username, tg_id)]
    await redis.set(_cache_key(tg_id), json.dumps([c.username for c in absent]), ex=CACHE_TTL_S)
    return absent


async def forget(redis: Redis, tg_id: int) -> None:
    """Drop the cached answer, so "I joined" is believed immediately."""
    await redis.delete(_cache_key(tg_id))
