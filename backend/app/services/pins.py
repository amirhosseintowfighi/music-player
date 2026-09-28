"""Pinned to the top of the library: up to four playlists or artists, like Spotify's.

A pin is the listener's own shortcut, so only something they can open is pinned: a
playlist they own or collaborate on (or one that was shared with them and is still
public), or an artist that exists. Pins of things that later disappear are dropped
on read, not kept as dead rows on screen.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, NotFound
from app.models import Artist, LibraryPin
from app.services import playlists

PinKind = Literal["playlist", "artist"]
MAX_PINS = 4


async def _check(session: AsyncSession, user_id: int, kind: PinKind, ref_id: int) -> int:
    """The id to pin (an artist that was merged is pinned as the one it became)."""
    if kind == "playlist":
        await playlists.access(session, ref_id, user_id)
        return ref_id
    artist = await session.get(Artist, ref_id)
    if artist is not None and artist.merged_into_id is not None:
        artist = await session.get(Artist, artist.merged_into_id)
    if artist is None or artist.hidden:
        raise NotFound("artist not found")
    return int(artist.id)


async def pin(session: AsyncSession, user_id: int, kind: PinKind, ref_id: int) -> None:
    target = await _check(session, user_id, kind, ref_id)
    already = await session.scalar(
        select(LibraryPin.ref_id).where(
            LibraryPin.user_id == user_id, LibraryPin.kind == kind, LibraryPin.ref_id == target
        )
    )
    if already is not None:
        return
    count = await session.scalar(
        select(func.count()).select_from(LibraryPin).where(LibraryPin.user_id == user_id)
    )
    if int(count or 0) >= MAX_PINS:
        raise Conflict("you can pin up to four things", reason="too_many", limit=MAX_PINS)
    await session.execute(
        insert(LibraryPin)
        .values(user_id=user_id, kind=kind, ref_id=target)
        .on_conflict_do_nothing()
    )


async def unpin(session: AsyncSession, user_id: int, kind: PinKind, ref_id: int) -> None:
    await session.execute(
        delete(LibraryPin).where(
            LibraryPin.user_id == user_id, LibraryPin.kind == kind, LibraryPin.ref_id == ref_id
        )
    )


async def list_for(session: AsyncSession, user_id: int) -> list[tuple[PinKind, int]]:
    """This user's pins, oldest first (the order they were pinned in)."""
    rows = await session.execute(
        select(LibraryPin.kind, LibraryPin.ref_id)
        .where(LibraryPin.user_id == user_id)
        .order_by(LibraryPin.pinned_at, LibraryPin.kind, LibraryPin.ref_id)
    )
    out: list[tuple[PinKind, int]] = []
    for kind, ref_id in rows.tuples():
        try:
            await _check(session, user_id, "playlist" if kind == "playlist" else "artist", ref_id)
        except NotFound:
            continue
        out.append(("playlist" if kind == "playlist" else "artist", int(ref_id)))
    return out
