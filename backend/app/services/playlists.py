"""Playlists: ordering, sharing and collaboration.

Order uses a fractional index (`playlist_tracks.position`): moving one track rewrites
one row instead of renumbering the list. Positions are re-spread when two neighbours
get too close to split again.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, Forbidden, InvalidInput, LimitReached, NotFound
from app.models import Playlist, PlaylistCollaborator, PlaylistTrack, Track, User
from app.services import plans, users

STEP = Decimal(1024)
MIN_GAP = Decimal("0.000001")
MAX_TRACKS = 5000
SLUG_ALPHABET = "abcdefghijkmnopqrstuvwxyz23456789"


@dataclass(frozen=True, slots=True)
class Access:
    playlist: Playlist
    can_edit: bool


def new_slug() -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(10))


async def _plan(session: AsyncSession, user_id: int) -> plans.PlanInfo:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    return await plans.get_plan(session, users.effective_plan(user))


async def create(
    session: AsyncSession, user_id: int, name: str, description: str | None = None
) -> Playlist:
    plan = await _plan(session, user_id)
    count = await session.scalar(
        select(func.count())
        .select_from(Playlist)
        .where(Playlist.user_id == user_id, Playlist.kind == "manual")
    )
    if not plan.allows("playlists", count or 0):
        raise LimitReached(
            "playlist limit reached", kind="playlists", limit=plan.limit("playlists")
        )
    playlist = Playlist(user_id=user_id, name=name.strip()[:100], description=description)
    session.add(playlist)
    await session.flush()
    return playlist


async def access(session: AsyncSession, playlist_id: int, user_id: int | None) -> Access:
    """Resolves what ``user_id`` may do with a playlist. Public ones are readable by all."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None:
        raise NotFound("playlist not found")
    if playlist.user_id == user_id:
        return Access(playlist, True)
    collaborator = None
    if user_id is not None and playlist.is_collaborative:
        collaborator = await session.get(PlaylistCollaborator, (playlist_id, user_id))
    if collaborator is not None:
        return Access(playlist, collaborator.role == "editor")
    if playlist.is_public:
        return Access(playlist, False)
    raise NotFound("playlist not found")  # never leak the existence of a private playlist


async def by_slug(session: AsyncSession, slug: str) -> Playlist:
    playlist = (
        await session.scalars(select(Playlist).where(Playlist.share_slug == slug))
    ).one_or_none()
    if playlist is None or not playlist.is_public:
        raise NotFound("playlist not found")
    return playlist


async def list_for_user(session: AsyncSession, user_id: int) -> list[Playlist]:
    stmt = (
        select(Playlist)
        .outerjoin(
            PlaylistCollaborator,
            (PlaylistCollaborator.playlist_id == Playlist.id)
            & (PlaylistCollaborator.user_id == user_id),
        )
        .where((Playlist.user_id == user_id) | (PlaylistCollaborator.user_id == user_id))
        .order_by(Playlist.updated_at.desc())
    )
    return list((await session.scalars(stmt)).all())


async def update_details(
    session: AsyncSession,
    playlist_id: int,
    user_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    is_public: bool | None = None,
    is_collaborative: bool | None = None,
) -> Playlist:
    found = await access(session, playlist_id, user_id)
    if found.playlist.user_id != user_id:
        raise Forbidden("only the owner can change a playlist")
    playlist = found.playlist
    plan = await _plan(session, user_id)
    if name is not None:
        playlist.name = name.strip()[:100] or playlist.name
    if description is not None:
        playlist.description = description[:500] or None
    if is_public is not None:
        if is_public and "share_playlist" not in plan.features:
            raise LimitReached("sharing is a Pro feature", kind="share_playlist", limit=0)
        playlist.is_public = is_public
        if is_public and not playlist.share_slug:
            playlist.share_slug = new_slug()
    if is_collaborative is not None:
        if is_collaborative and "collab_playlist" not in plan.features:
            raise LimitReached("collaboration is a Pro feature", kind="collab_playlist", limit=0)
        playlist.is_collaborative = is_collaborative
    await session.execute(
        update(Playlist).where(Playlist.id == playlist_id).values(updated_at=func.now())
    )
    await session.flush()
    return playlist


async def remove(session: AsyncSession, playlist_id: int, user_id: int) -> None:
    found = await access(session, playlist_id, user_id)
    if found.playlist.user_id != user_id:
        raise Forbidden("only the owner can delete a playlist")
    await session.delete(found.playlist)


async def _next_position(session: AsyncSession, playlist_id: int) -> Decimal:
    last = await session.scalar(
        select(func.max(PlaylistTrack.position)).where(PlaylistTrack.playlist_id == playlist_id)
    )
    return (Decimal(last) if last is not None else Decimal(0)) + STEP


async def add_tracks(
    session: AsyncSession, playlist_id: int, user_id: int, track_ids: list[int]
) -> int:
    """Appends tracks, skipping ones already in the playlist. Returns how many were added."""
    found = await access(session, playlist_id, user_id)
    if not found.can_edit:
        raise Forbidden("no write access to this playlist")
    if not track_ids:
        return 0
    # Always store the canonical id so the same song cannot appear twice.
    rows = (
        await session.execute(
            select(Track.id, func.coalesce(Track.canonical_track_id, Track.id)).where(
                Track.id.in_(track_ids), Track.hidden.is_(False)
            )
        )
    ).all()
    canonical = {asked: root for asked, root in rows}
    ordered = list(dict.fromkeys(canonical[t] for t in track_ids if t in canonical))
    if not ordered:
        raise NotFound("no playable track in the request")
    if found.playlist.tracks_count + len(ordered) > MAX_TRACKS:
        raise Conflict("playlist is full", limit=MAX_TRACKS)

    position = await _next_position(session, playlist_id)
    values = []
    for track_id in ordered:
        values.append(
            {
                "playlist_id": playlist_id,
                "track_id": track_id,
                "position": position,
                "added_by": user_id,
            }
        )
        position += STEP
    result = await session.execute(
        insert(PlaylistTrack)
        .values(values)
        .on_conflict_do_nothing()
        .returning(PlaylistTrack.track_id)
    )
    added = len(result.all())
    if added:
        await refresh_counts(session, playlist_id)
    return added


async def remove_track(
    session: AsyncSession, playlist_id: int, user_id: int, track_id: int
) -> None:
    found = await access(session, playlist_id, user_id)
    if not found.can_edit:
        raise Forbidden("no write access to this playlist")
    result = await session.execute(
        delete(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == playlist_id, PlaylistTrack.track_id == track_id)
        .returning(PlaylistTrack.track_id)
    )
    if result.first() is None:
        raise NotFound("track is not in this playlist")
    await refresh_counts(session, playlist_id)


async def move_track(
    session: AsyncSession, playlist_id: int, user_id: int, track_id: int, after_track_id: int | None
) -> None:
    """Moves ``track_id`` right after ``after_track_id`` (or to the top when None)."""
    found = await access(session, playlist_id, user_id)
    if not found.can_edit:
        raise Forbidden("no write access to this playlist")
    if track_id == after_track_id:
        raise InvalidInput("cannot move a track after itself")

    rows = (
        await session.execute(
            select(PlaylistTrack.track_id, PlaylistTrack.position)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position)
        )
    ).all()
    positions = {track: Decimal(position) for track, position in rows}
    if track_id not in positions:
        raise NotFound("track is not in this playlist")
    if after_track_id is not None and after_track_id not in positions:
        raise NotFound("anchor track is not in this playlist")

    order = [track for track, _ in rows if track != track_id]
    index = 0 if after_track_id is None else order.index(after_track_id) + 1
    before = positions[order[index - 1]] if index > 0 else Decimal(0)
    after = positions[order[index]] if index < len(order) else before + STEP * 2
    new_position = (before + after) / 2

    if after - before < MIN_GAP:
        # The gap collapsed after many moves: re-spread the whole list once.
        order.insert(index, track_id)
        await session.execute(
            text(
                "UPDATE playlist_tracks SET position = v.pos FROM ("
                "SELECT unnest(CAST(:ids AS bigint[])) AS track_id, "
                "generate_series(1, array_length(CAST(:ids AS bigint[]), 1)) * :step AS pos"
                ") v WHERE playlist_tracks.playlist_id = :pid AND playlist_tracks.track_id = v.track_id"
            ).bindparams(ids=order, step=int(STEP), pid=playlist_id)
        )
        return

    await session.execute(
        update(PlaylistTrack)
        .where(PlaylistTrack.playlist_id == playlist_id, PlaylistTrack.track_id == track_id)
        .values(position=new_position)
    )
    await session.execute(
        update(Playlist).where(Playlist.id == playlist_id).values(updated_at=func.now())
    )


async def track_ids(session: AsyncSession, playlist_id: int) -> list[int]:
    return list(
        (
            await session.scalars(
                select(PlaylistTrack.track_id)
                .where(PlaylistTrack.playlist_id == playlist_id)
                .order_by(PlaylistTrack.position)
            )
        ).all()
    )


async def refresh_counts(session: AsyncSession, playlist_id: int) -> None:
    await session.execute(
        text(
            "UPDATE playlists p SET tracks_count = c.n, duration_total = c.secs, updated_at = now() "
            "FROM (SELECT count(*) AS n, COALESCE(sum(t.duration), 0) AS secs "
            "      FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
            "      WHERE pt.playlist_id = :pid) c "
            "WHERE p.id = :pid"
        ).bindparams(pid=playlist_id)
    )


async def add_collaborator(
    session: AsyncSession, playlist_id: int, owner_id: int, user_id: int
) -> None:
    found = await access(session, playlist_id, owner_id)
    if found.playlist.user_id != owner_id:
        raise Forbidden("only the owner can invite")
    if not found.playlist.is_collaborative:
        raise InvalidInput("playlist is not collaborative")
    await session.execute(
        insert(PlaylistCollaborator)
        .values(playlist_id=playlist_id, user_id=user_id, role="editor")
        .on_conflict_do_nothing()
    )
