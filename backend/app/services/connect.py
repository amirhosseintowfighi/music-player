"""Connect: see where you are listening, and move the music between your devices.

Every open copy of the Mini App (the phone, Telegram Desktop, a tablet) sends a
heartbeat every few seconds with what it is playing. The answer is the list of the
listener's other live devices, plus any commands another device left for this one:
"play this queue from 1:12", "pause", "next". That is Spotify Connect's shape, with
a poll instead of a socket, which is what a Mini App can keep alive.

State lives in Redis only and dies on its own: a device that stops calling in drops
off the list after ``STALE_S``, and an undelivered command after ``COMMAND_TTL_S``.
One limitation is Telegram's, not ours: a phone that puts the app in the background
may stop running it, and then it is not a device anyone can reach until it is back.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from redis.asyncio import Redis

from app.errors import NotFound
from app.redis_util import resolve

STALE_S = 30
COMMAND_TTL_S = 60
KEY_TTL_S = 3600
MAX_DEVICES = 10
MAX_COMMANDS = 20

DeviceKind = Literal["phone", "tablet", "desktop", "web"]
Action = Literal["transfer", "play", "pause", "next", "previous", "seek"]


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    kind: DeviceKind
    seen: float
    track_id: int | None = None
    position_s: float = 0.0
    playing: bool = False


@dataclass(frozen=True, slots=True)
class Command:
    action: Action
    sender: str
    track_ids: tuple[int, ...] = ()
    index: int = 0
    position_s: float = 0.0
    playing: bool = True


def _devices_key(user_id: int) -> str:
    return f"connect:{user_id}:devices"


def _commands_key(user_id: int, device_id: str) -> str:
    return f"connect:{user_id}:cmd:{device_id}"


def _device(raw: str) -> Device | None:
    try:
        data: dict[str, Any] = json.loads(raw)
        return Device(
            id=str(data["id"]),
            name=str(data["name"]),
            kind=data["kind"] if data.get("kind") in ("phone", "tablet", "desktop") else "web",
            seen=float(data["seen"]),
            track_id=int(data["track_id"]) if data.get("track_id") else None,
            position_s=float(data.get("position_s") or 0),
            playing=bool(data.get("playing")),
        )
    except (ValueError, KeyError, TypeError):
        return None


async def devices(redis: Redis, user_id: int, now: float | None = None) -> list[Device]:
    """Live devices, the one heard from most recently first. Stale ones are cleared."""
    moment = now if now is not None else time.time()
    raw: dict[str, str] = await resolve(redis.hgetall(_devices_key(user_id)))
    live: list[Device] = []
    stale: list[str] = []
    for device_id, value in raw.items():
        device = _device(value)
        if device is None or moment - device.seen > STALE_S:
            stale.append(device_id)
        else:
            live.append(device)
    if stale:
        await resolve(redis.hdel(_devices_key(user_id), *stale))
    return sorted(live, key=lambda d: -d.seen)


async def heartbeat(
    redis: Redis, user_id: int, device: Device, now: float | None = None
) -> tuple[list[Device], list[Command]]:
    """Records this device, and hands back the others and the commands left for it."""
    moment = now if now is not None else time.time()
    stamped = Device(**{**asdict(device), "seen": moment})
    key = _devices_key(user_id)
    known = await devices(redis, user_id, moment)
    if device.id not in {d.id for d in known} and len(known) >= MAX_DEVICES:
        # Someone with a dozen tabs open: the one heard from longest ago makes room.
        await resolve(redis.hdel(key, known[-1].id))
    await resolve(redis.hset(key, device.id, json.dumps(asdict(stamped))))
    await redis.expire(key, KEY_TTL_S)

    inbox = _commands_key(user_id, device.id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lrange(inbox, 0, -1)
        pipe.delete(inbox)
        raw_commands, _ = await pipe.execute()
    commands: list[Command] = []
    for raw in raw_commands:
        try:
            data = json.loads(raw)
            commands.append(Command(**{**data, "track_ids": tuple(data.get("track_ids") or ())}))
        except (ValueError, TypeError):
            continue
    others = [d for d in await devices(redis, user_id, moment) if d.id != device.id]
    return others, commands


async def send(redis: Redis, user_id: int, target: str, command: Command) -> None:
    """Leaves a command for one of this user's live devices."""
    if target not in {d.id for d in await devices(redis, user_id)}:
        raise NotFound("that device is not listening any more")
    inbox = _commands_key(user_id, target)
    body = json.dumps({**asdict(command), "track_ids": list(command.track_ids)})
    async with redis.pipeline(transaction=True) as pipe:
        pipe.rpush(inbox, body)
        pipe.ltrim(inbox, -MAX_COMMANDS, -1)
        pipe.expire(inbox, COMMAND_TTL_S)
        await pipe.execute()


async def forget(redis: Redis, user_id: int, device_id: str) -> None:
    """The app is closing: drop off the list now instead of in half a minute."""
    await resolve(redis.hdel(_devices_key(user_id), device_id))
    await redis.delete(_commands_key(user_id, device_id))
