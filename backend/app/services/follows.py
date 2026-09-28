"""Following artists, and the new music that comes from it.

A follow is the strongest thing a listener can say about an artist, so it feeds
Release Radar and a notice the day one of their artists gets a new track.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound
from app.models import Artist, ArtistFollow
from app.services import notifications

MAX_FOLLOWS = 1000


async def _root(session: AsyncSession, artist_id: int) -> int:
    """Follows land on the artist a duplicate was merged into."""
    row = (
        await session.execute(
            select(Artist.id, Artist.merged_into_id).where(
                Artist.id == artist_id, Artist.hidden.is_(False)
            )
        )
    ).first()
    if row is None:
        raise NotFound("artist not found")
    return int(row.merged_into_id or row.id)


async def follow(session: AsyncSession, user_id: int, artist_id: int) -> bool:
    target = await _root(session, artist_id)
    result = await session.execute(
        insert(ArtistFollow)
        .values(user_id=user_id, artist_id=target)
        .on_conflict_do_nothing()
        .returning(ArtistFollow.artist_id)
    )
    return result.first() is not None


async def unfollow(session: AsyncSession, user_id: int, artist_id: int) -> bool:
    target = await _root(session, artist_id)
    result = await session.execute(
        delete(ArtistFollow)
        .where(ArtistFollow.user_id == user_id, ArtistFollow.artist_id == target)
        .returning(ArtistFollow.artist_id)
    )
    return result.first() is not None


async def is_following(session: AsyncSession, user_id: int, artist_id: int) -> bool:
    target = await _root(session, artist_id)
    found = await session.scalar(
        select(ArtistFollow.artist_id).where(
            ArtistFollow.user_id == user_id, ArtistFollow.artist_id == target
        )
    )
    return found is not None


async def followers_count(session: AsyncSession, artist_id: int) -> int:
    target = await _root(session, artist_id)
    count = await session.scalar(
        text("SELECT count(*) FROM artist_follows WHERE artist_id = :a").bindparams(a=target)
    )
    return int(count or 0)


async def followed_ids(session: AsyncSession, user_id: int) -> list[int]:
    rows = await session.scalars(
        select(ArtistFollow.artist_id)
        .where(ArtistFollow.user_id == user_id)
        .order_by(ArtistFollow.created_at.desc())
        .limit(MAX_FOLLOWS)
    )
    return list(rows.all())


async def queue_new_releases(session: AsyncSession, since_hours: int = 24) -> int:
    """One notice per follower per artist per day, when that artist got new music."""
    rows = await session.execute(
        text(
            """
        SELECT f.user_id, a.id AS artist_id, a.name AS artist,
               count(DISTINCT t.id) AS new_tracks,
               (array_agg(t.title ORDER BY t.created_at DESC))[1] AS latest
          FROM artist_follows f
          JOIN artists a ON a.id = f.artist_id
          JOIN track_artists ta ON ta.artist_id = a.id
          JOIN tracks t ON t.id = ta.track_id
         WHERE t.created_at > now() - make_interval(hours => :hours)
           AND NOT t.hidden AND t.canonical_track_id IS NULL
         GROUP BY 1, 2, 3
        """
        ).bindparams(hours=since_hours)
    )
    day = datetime.now(UTC).strftime("%Y%m%d")
    notices = [
        notifications.Notice(
            user_id=row.user_id,
            kind="new_release",
            payload={
                "artist": row.artist,
                "artist_id": row.artist_id,
                "count": row.new_tracks,
                "title": row.latest,
            },
            dedup_key=f"rel:{row.artist_id}:{day}",
        )
        for row in rows
    ]
    return await notifications.enqueue(session, notices)
