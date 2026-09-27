"""Jam: people listening to one queue together, in step.

A jam is a shared queue plus one playhead. It lives in Redis, not Postgres: it is
worth nothing once everybody has gone home, and the expiry that cleans it up is the
same one that keeps an abandoned jam from lingering.

The playhead is stored as *where it was, and when* (``pos`` at server time ``at``)
rather than as a ticking number, so nobody has to report progress for everybody else
to stay in step: any reader computes where the music is now. That also lets the
server move the jam on to the next track on its own when one ends, so a jam keeps
going even while the host's phone is locked.

Every mutation runs as an optimistic transaction (WATCH/MULTI) and bumps ``rev``;
``qrev`` bumps only when the queue itself changes, so the clients that poll can skip
re-reading a queue they already have.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import WatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.models import Track, User

TTL_S = 6 * 3600
MAX_QUEUE = 500
MAX_MEMBERS = 50
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LEN = 6
_RETRIES = 8

Action = Literal["play", "pause", "seek", "next", "previous", "jump"]


def _key(code: str) -> str:
    return f"jam:{code}"


def _member_key(user_id: int) -> str:
    return f"jam:user:{user_id}"


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class Item:
    track_id: int
    duration: int
    added_by: int


@dataclass(slots=True)
class Member:
    user_id: int
    first_name: str
    username: str | None
    joined_ms: int


@dataclass(slots=True)
class Jam:
    code: str
    host_id: int
    created_ms: int
    guests_can_control: bool = True
    items: list[Item] = field(default_factory=list)
    index: int = 0
    playing: bool = False
    pos: float = 0.0
    at: int = 0
    rev: int = 0
    qrev: int = 0
    members: dict[int, Member] = field(default_factory=dict)

    def position(self, now: int) -> float:
        if not self.playing:
            return self.pos
        return self.pos + max(0, now - self.at) / 1000

    def can_control(self, user_id: int) -> bool:
        return user_id == self.host_id or (self.guests_can_control and user_id in self.members)

    def current(self) -> Item | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def to_json(self) -> str:
        return json.dumps(
            {
                "code": self.code,
                "host": self.host_id,
                "created": self.created_ms,
                "gcc": self.guests_can_control,
                "items": [[i.track_id, i.duration, i.added_by] for i in self.items],
                "index": self.index,
                "playing": self.playing,
                "pos": self.pos,
                "at": self.at,
                "rev": self.rev,
                "qrev": self.qrev,
                "members": [
                    [m.user_id, m.first_name, m.username, m.joined_ms]
                    for m in self.members.values()
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> Jam:
        data: dict[str, Any] = json.loads(raw)
        return cls(
            code=data["code"],
            host_id=data["host"],
            created_ms=data["created"],
            guests_can_control=data["gcc"],
            items=[Item(t, d, b) for t, d, b in data["items"]],
            index=data["index"],
            playing=data["playing"],
            pos=data["pos"],
            at=data["at"],
            rev=data["rev"],
            qrev=data["qrev"],
            members={m[0]: Member(m[0], m[1], m[2], m[3]) for m in data["members"]},
        )


def settle(jam: Jam, now: int) -> bool:
    """Move the playhead past every track that has finished since it was last read.

    Returns True when anything changed. A track with no known duration never ends on
    its own; the listeners' players say when it did (the ``next`` action).
    """
    changed = False
    while jam.playing:
        item = jam.current()
        if item is None or item.duration <= 0:
            break
        elapsed = jam.position(now)
        if elapsed < item.duration:
            break
        changed = True
        if jam.index + 1 >= len(jam.items):
            # The queue ran out: stop at the end of the last track.
            jam.playing, jam.pos, jam.at = False, float(item.duration), now
            break
        jam.index += 1
        jam.pos = elapsed - item.duration
        jam.at = now - int(jam.pos * 1000)
        jam.pos = 0.0
    if changed:
        jam.rev += 1
    return changed


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def normalize_code(code: str) -> str:
    cleaned = code.strip().lower()
    if len(cleaned) != CODE_LEN or any(c not in CODE_ALPHABET for c in cleaned):
        raise NotFound("jam not found")
    return cleaned


async def load(redis: Redis, code: str) -> Jam:
    raw = await redis.get(_key(normalize_code(code)))
    if raw is None:
        raise NotFound("jam not found")
    jam = Jam.from_json(raw)
    settle(jam, now_ms())
    return jam


async def code_of(redis: Redis, user_id: int) -> str | None:
    code: str | None = await redis.get(_member_key(user_id))
    return code


async def _mutate(redis: Redis, code: str, change: Callable[[Jam, int], None]) -> Jam:
    """Apply ``change`` atomically; retried when another writer got there first."""
    key = _key(normalize_code(code))
    for _ in range(_RETRIES):
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if raw is None:
                    raise NotFound("jam not found")
                jam = Jam.from_json(raw)
                now = now_ms()
                settle(jam, now)
                change(jam, now)
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.set(key, jam.to_json(), ex=TTL_S)
                for member in jam.members:
                    pipe.set(_member_key(member), jam.code, ex=TTL_S)
                await pipe.execute()
                return jam
            except WatchError:
                continue
    raise Conflict("the jam is busy, try again")


async def _items(session: AsyncSession, track_ids: Sequence[int], added_by: int) -> list[Item]:
    """Only real, visible tracks get into a queue; order and repeats are kept."""
    if not track_ids:
        return []
    rows = await session.execute(
        select(Track.id, Track.duration).where(
            Track.id.in_(set(track_ids)), Track.hidden.is_(False)
        )
    )
    durations = {tid: int(d or 0) for tid, d in rows.tuples()}
    return [Item(tid, durations[tid], added_by) for tid in track_ids if tid in durations]


async def _member(session: AsyncSession, user_id: int, now: int) -> Member:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    return Member(user_id, user.first_name, user.username, now)


async def _drop_membership(redis: Redis, user_id: int) -> None:
    """Leave whatever jam this user is in (ending it if they host it)."""
    code = await code_of(redis, user_id)
    if code is None:
        return
    try:
        await leave(redis, code, user_id)
    except NotFound:
        await redis.delete(_member_key(user_id))


async def create(
    session: AsyncSession,
    redis: Redis,
    host_id: int,
    track_ids: Sequence[int] = (),
    index: int = 0,
    position_s: float = 0.0,
    playing: bool = False,
) -> Jam:
    """Start a jam, seeded with whatever the host is listening to right now."""
    await _drop_membership(redis, host_id)
    now = now_ms()
    items = (await _items(session, track_ids[:MAX_QUEUE], host_id))[:MAX_QUEUE]
    jam = Jam(
        code=_new_code(),
        host_id=host_id,
        created_ms=now,
        items=items,
        index=min(max(index, 0), max(len(items) - 1, 0)),
        playing=playing and bool(items),
        pos=max(0.0, position_s) if items else 0.0,
        at=now,
        members={host_id: await _member(session, host_id, now)},
    )
    for _ in range(_RETRIES):
        if await redis.set(_key(jam.code), jam.to_json(), ex=TTL_S, nx=True):
            await redis.set(_member_key(host_id), jam.code, ex=TTL_S)
            return jam
        jam.code = _new_code()
    raise Conflict("could not allocate a jam code")  # pragma: no cover


async def join(session: AsyncSession, redis: Redis, code: str, user_id: int) -> Jam:
    code = normalize_code(code)
    current = await code_of(redis, user_id)
    if current is not None and current != code:
        await _drop_membership(redis, user_id)
    member = await _member(session, user_id, now_ms())

    def change(jam: Jam, _: int) -> None:
        if user_id in jam.members:
            return
        if len(jam.members) >= MAX_MEMBERS:
            raise Forbidden("this jam is full", reason="jam_full")
        jam.members[user_id] = member
        jam.rev += 1

    return await _mutate(redis, code, change)


async def leave(redis: Redis, code: str, user_id: int) -> bool:
    """Returns True when the jam ended (the host left, or the last listener did)."""
    code = normalize_code(code)
    jam = await load(redis, code)
    if user_id == jam.host_id:
        await end(redis, code, user_id)
        return True

    def change(j: Jam, _: int) -> None:
        if j.members.pop(user_id, None) is not None:
            j.rev += 1

    await _mutate(redis, code, change)
    await redis.delete(_member_key(user_id))
    return False


async def end(redis: Redis, code: str, user_id: int) -> None:
    jam = await load(redis, code)
    if user_id != jam.host_id:
        raise Forbidden("only the host can end the jam", reason="not_host")
    await redis.delete(_key(jam.code), *[_member_key(m) for m in jam.members])


def _require_member(jam: Jam, user_id: int) -> None:
    if user_id not in jam.members:
        raise Forbidden("join the jam first", reason="not_member")


def _require_control(jam: Jam, user_id: int) -> None:
    _require_member(jam, user_id)
    if not jam.can_control(user_id):
        raise Forbidden("the host controls playback", reason="host_only")


def _go_to(jam: Jam, index: int, now: int) -> None:
    jam.index, jam.pos, jam.at, jam.playing = index, 0.0, now, True


async def control(
    redis: Redis,
    code: str,
    user_id: int,
    action: Action,
    position_s: float | None = None,
    index: int | None = None,
    expected_index: int | None = None,
) -> Jam:
    """Play, pause, seek or skip for everybody.

    ``expected_index`` makes a skip idempotent: every listener's player reports the
    end of the same track, and only the first report may move the jam on.
    """

    def change(jam: Jam, now: int) -> None:
        _require_control(jam, user_id)
        if expected_index is not None and expected_index != jam.index:
            return
        here = jam.position(now)
        if action == "play":
            if jam.current() is None:
                return
            jam.pos, jam.at, jam.playing = here, now, True
        elif action == "pause":
            jam.pos, jam.at, jam.playing = here, now, False
        elif action == "seek":
            if position_s is None:
                raise InvalidInput("position_s is required")
            item = jam.current()
            limit = float(item.duration) if item and item.duration > 0 else position_s
            jam.pos, jam.at = max(0.0, min(position_s, limit)), now
        elif action == "next":
            if jam.index + 1 >= len(jam.items):
                jam.pos, jam.at, jam.playing = here, now, False
            else:
                _go_to(jam, jam.index + 1, now)
        elif action == "previous":
            if here > 3 or jam.index == 0:
                jam.pos, jam.at = 0.0, now
            else:
                _go_to(jam, jam.index - 1, now)
        elif action == "jump":
            if index is None or not 0 <= index < len(jam.items):
                raise InvalidInput("index is out of range")
            _go_to(jam, index, now)
        jam.rev += 1

    return await _mutate(redis, code, change)


async def add(
    session: AsyncSession,
    redis: Redis,
    code: str,
    user_id: int,
    track_ids: Sequence[int],
    position: Literal["next", "end", "now"] = "end",
) -> Jam:
    """Anyone in the jam can add music; ``now`` also skips to it (controllers only)."""
    new = await _items(session, track_ids, user_id)
    if not new:
        raise InvalidInput("no playable tracks")

    def change(jam: Jam, now: int) -> None:
        _require_member(jam, user_id)
        if position == "now":
            _require_control(jam, user_id)
        if len(jam.items) + len(new) > MAX_QUEUE:
            raise Forbidden("the queue is full", reason="queue_full")
        if not jam.items:
            jam.items = list(new)
            _go_to(jam, 0, now)
        elif position == "end":
            jam.items.extend(new)
        else:
            at = jam.index + 1
            jam.items[at:at] = new
            if position == "now":
                _go_to(jam, at, now)
        jam.qrev += 1
        jam.rev += 1

    return await _mutate(redis, code, change)


async def remove(redis: Redis, code: str, user_id: int, index: int) -> Jam:
    """The host removes anything; a guest only what they added themselves."""

    def change(jam: Jam, now: int) -> None:
        _require_member(jam, user_id)
        if not 0 <= index < len(jam.items):
            raise InvalidInput("index is out of range")
        if index == jam.index:
            raise InvalidInput("skip the playing track instead of removing it")
        if user_id != jam.host_id and jam.items[index].added_by != user_id:
            raise Forbidden("only the host can remove this", reason="host_only")
        del jam.items[index]
        if index < jam.index:
            jam.index -= 1
        jam.qrev += 1
        jam.rev += 1

    return await _mutate(redis, code, change)


async def configure(redis: Redis, code: str, user_id: int, guests_can_control: bool) -> Jam:
    def change(jam: Jam, _: int) -> None:
        if user_id != jam.host_id:
            raise Forbidden("only the host can change this", reason="not_host")
        jam.guests_can_control = guests_can_control
        jam.rev += 1

    return await _mutate(redis, code, change)
