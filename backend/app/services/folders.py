"""Playlist folders: one level of filing for a library that has grown."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, LimitReached, NotFound
from app.models import Playlist, PlaylistFolder

MAX_FOLDERS = 100


async def list_for(session: AsyncSession, user_id: int) -> list[tuple[PlaylistFolder, int]]:
    """Folders with how many playlists each holds, oldest first (the order they were made)."""
    counts = (
        select(Playlist.folder_id, func.count().label("n"))
        .where(Playlist.user_id == user_id, Playlist.folder_id.is_not(None))
        .group_by(Playlist.folder_id)
        .subquery()
    )
    rows = await session.execute(
        select(PlaylistFolder, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.folder_id == PlaylistFolder.id)
        .where(PlaylistFolder.user_id == user_id)
        .order_by(PlaylistFolder.id)
    )
    return [(folder, int(n)) for folder, n in rows.tuples()]


async def _mine(session: AsyncSession, folder_id: int, user_id: int) -> PlaylistFolder:
    folder = await session.get(PlaylistFolder, folder_id)
    if folder is None or folder.user_id != user_id:
        raise NotFound("folder not found")
    return folder


async def create(session: AsyncSession, user_id: int, name: str) -> PlaylistFolder:
    count = await session.scalar(
        select(func.count()).select_from(PlaylistFolder).where(PlaylistFolder.user_id == user_id)
    )
    if (count or 0) >= MAX_FOLDERS:
        raise LimitReached("too many folders", kind="folders", limit=MAX_FOLDERS)
    folder = PlaylistFolder(user_id=user_id, name=name.strip())
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def rename(session: AsyncSession, folder_id: int, user_id: int, name: str) -> PlaylistFolder:
    folder = await _mine(session, folder_id, user_id)
    folder.name = name.strip()
    return folder


async def remove(session: AsyncSession, folder_id: int, user_id: int) -> None:
    """Deleting a folder keeps its playlists; they just go back to the top level."""
    folder = await _mine(session, folder_id, user_id)
    await session.execute(
        update(Playlist).where(Playlist.folder_id == folder.id).values(folder_id=None)
    )
    await session.delete(folder)


async def file_playlist(
    session: AsyncSession, playlist_id: int, user_id: int, folder_id: int | None
) -> Playlist:
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None:
        raise NotFound("playlist not found")
    if playlist.user_id != user_id:
        raise Forbidden("only the owner can file a playlist")
    if folder_id is not None:
        await _mine(session, folder_id, user_id)
    playlist.folder_id = folder_id
    return playlist
