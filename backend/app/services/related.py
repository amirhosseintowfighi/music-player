"""Around a song or an artist: "Fans also like" and a song's credits.

**Fans also like** is built from our own listeners, in three passes, each only
filling what the one before could not:

1. the item-item similarity matrix, rolled up from tracks to their artists (the
   strongest signal: people who play this artist's songs play those songs too);
2. co-listening: other artists this artist's listeners played in the last half-year;
3. collaborators: artists credited on the same songs.

**Credits** are everything we know about who made a song, plus where it came from:
the channels that carry it, the ones with the most subscribers first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound
from app.services import history

RELATED_LIMIT = 12
# Past this many listeners the co-listening pass is a sample, not a census.
FAN_SAMPLE = 2000
MAX_SOURCES = 5

_ARTIST_OK = "a.merged_into_id IS NULL AND NOT a.hidden"


async def _root_artist(session: AsyncSession, artist_id: int) -> int:
    root = await session.scalar(
        text("SELECT COALESCE(merged_into_id, id) FROM artists WHERE id = :aid").bindparams(
            aid=artist_id
        )
    )
    if root is None:
        raise NotFound("artist not found")
    return int(root)


async def related_artist_ids(
    session: AsyncSession, artist_id: int, limit: int = RELATED_LIMIT
) -> list[int]:
    root = await _root_artist(session, artist_id)
    passes = [
        # 1. similar tracks, rolled up to their primary artists
        f"""
        SELECT ta2.artist_id, sum(s.score) AS score
          FROM track_artists ta
          JOIN track_similarity s ON s.track_id = ta.track_id
          JOIN track_artists ta2 ON ta2.track_id = s.similar_id AND ta2.role = 'primary'
          JOIN artists a ON a.id = ta2.artist_id
         WHERE ta.artist_id = :aid AND ta2.artist_id <> :aid AND {_ARTIST_OK}
         GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT :limit
        """,
        # 2. what this artist's listeners also play
        f"""
        WITH fans AS (
            SELECT DISTINCT h.user_id
              FROM play_history h
              JOIN track_artists ta ON ta.track_id = h.track_id AND ta.artist_id = :aid
             WHERE h.played_at > now() - interval '180 days'
             LIMIT {FAN_SAMPLE}
        )
        SELECT ta.artist_id, count(DISTINCT h.user_id) AS score
          FROM play_history h
          JOIN fans f ON f.user_id = h.user_id
          JOIN track_artists ta ON ta.track_id = h.track_id AND ta.role = 'primary'
          JOIN artists a ON a.id = ta.artist_id
         WHERE h.played_at > now() - interval '180 days'
           AND ta.artist_id <> :aid AND {_ARTIST_OK}
         GROUP BY 1 HAVING count(DISTINCT h.user_id) >= 2
         ORDER BY 2 DESC, 1 LIMIT :limit
        """,
        # 3. credited on the same songs
        f"""
        SELECT ta2.artist_id, count(*) AS score
          FROM track_artists ta
          JOIN track_artists ta2 ON ta2.track_id = ta.track_id AND ta2.artist_id <> :aid
          JOIN artists a ON a.id = ta2.artist_id
         WHERE ta.artist_id = :aid AND {_ARTIST_OK}
         GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT :limit
        """,
    ]
    found: list[int] = []
    for sql in passes:
        rows = await session.execute(text(sql).bindparams(aid=root, limit=limit))
        for row in rows:
            if row[0] not in found:
                found.append(int(row[0]))
        if len(found) >= limit:
            break
    return found[:limit]


@dataclass(frozen=True, slots=True)
class Source:
    channel_id: int
    username: str | None
    title: str
    subscribers_count: int
    posted_at: datetime


@dataclass(frozen=True, slots=True)
class Credits:
    track_id: int
    genre: str | None
    file_name: str | None
    mime_type: str | None
    file_size: int
    first_posted_at: datetime | None
    channels: int
    sources: list[Source]


async def credits(session: AsyncSession, track_id: int) -> Credits:
    root = await history.canonical_id(session, track_id)
    track = (
        await session.execute(
            text(
                "SELECT id, genre, file_name, mime_type, file_size, channels_count"
                " FROM tracks WHERE id = :id AND NOT hidden"
            ).bindparams(id=root)
        )
    ).one_or_none()
    if track is None:
        raise NotFound("track not found")
    rows = (
        await session.execute(
            text(
                """
            SELECT DISTINCT ON (c.id) c.id, c.username, c.title, c.subscribers_count,
                   ct.posted_at
              FROM channel_tracks ct
              JOIN tracks g ON g.id = ct.track_id
              JOIN channels c ON c.id = ct.channel_id
                             AND c.status IN ('pending', 'indexing', 'active')
             WHERE COALESCE(g.canonical_track_id, g.id) = :root
             ORDER BY c.id, ct.posted_at
            """
            ).bindparams(root=root)
        )
    ).all()
    sources = sorted(
        (
            Source(
                channel_id=int(row.id),
                username=row.username,
                title=row.title or (row.username or ""),
                subscribers_count=int(row.subscribers_count or 0),
                posted_at=row.posted_at,
            )
            for row in rows
        ),
        key=lambda s: (-s.subscribers_count, s.posted_at),
    )
    return Credits(
        track_id=root,
        genre=track.genre,
        file_name=track.file_name,
        mime_type=track.mime_type,
        file_size=int(track.file_size or 0),
        first_posted_at=min((s.posted_at for s in sources), default=None),
        channels=max(int(track.channels_count or 0), len(sources)),
        sources=sources[:MAX_SOURCES],
    )
