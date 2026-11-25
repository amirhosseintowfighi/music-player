"""Smart playlists: songs chosen by rules, kept up to date on their own (0018).

A smart playlist stores its rules (``schemas.SmartRules``) and writes the songs they
match into ``playlist_tracks``, exactly like a hand-made playlist. Everything that
reads playlists (playing, sharing, counts, folders) therefore works unchanged. The
list is rebuilt when the rules change, on request, and when it is opened after it
has gone stale.

The SQL is assembled from fixed fragments with bound values only: no user text is
ever part of the statement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, InvalidInput, LimitReached
from app.models import Playlist, PlaylistTrack
from app.schemas import SmartRules
from app.services import playlists

KIND = "smart"
STALE_AFTER = timedelta(hours=6)
# How far back "my plays" look. not_played_days is capped at a year too.
HISTORY_DAYS = 365

_SOURCES = {
    "library": """
        SELECT COALESCE(t.canonical_track_id, t.id) AS tid, max(ct.posted_at) AS at
          FROM user_channels uc
          JOIN channel_tracks ct ON ct.channel_id = uc.channel_id
          JOIN tracks t ON t.id = ct.track_id
         WHERE uc.user_id = :uid
         GROUP BY 1
    """,
    "likes": "SELECT l.track_id AS tid, l.created_at AS at FROM likes l WHERE l.user_id = :uid",
    "catalog": """
        SELECT t.id AS tid, t.created_at AS at
          FROM tracks t
         WHERE t.canonical_track_id IS NULL AND NOT t.hidden
    """,
}

_ORDER = {
    "recent": "src.at DESC NULLS LAST, r.id DESC",
    "popular": "r.plays_7d DESC, r.plays_total DESC, r.likes_count DESC, r.id DESC",
    "random": "random()",
    "most_played": "COALESCE(mine.n, 0) DESC, mine.last DESC NULLS LAST, r.id DESC",
    "least_played": "COALESCE(mine.n, 0) ASC, src.at DESC NULLS LAST, r.id DESC",
}


def _query(rules: SmartRules, user_id: int) -> tuple[str, dict[str, object]]:
    if (
        rules.min_duration is not None
        and rules.max_duration is not None
        and rules.min_duration > rules.max_duration
    ):
        raise InvalidInput("min_duration is above max_duration", reason="bad_rules")
    years = (rules.year_from, rules.year_to)
    if years[0] is not None and years[1] is not None and years[0] > years[1]:
        raise InvalidInput("year_from is after year_to", reason="bad_rules")

    params: dict[str, object] = {
        "uid": user_id,
        "lim": rules.limit,
        "hist": timedelta(days=HISTORY_DAYS),
    }
    where = [
        "NOT r.hidden",
        "r.canonical_track_id IS NULL",
        # Songs the listener hid (or snoozed, until the snooze ends) stay out.
        "NOT EXISTS (SELECT 1 FROM hidden_tracks h WHERE h.user_id = :uid "
        "AND h.track_id = r.id AND (h.until IS NULL OR h.until > now()))",
    ]
    if rules.artist_ids:
        where.append(
            "EXISTS (SELECT 1 FROM track_artists ta WHERE ta.track_id = r.id "
            "AND ta.artist_id = ANY(:artists))"
        )
        params["artists"] = rules.artist_ids
    if rules.channel_ids:
        where.append(
            "EXISTS (SELECT 1 FROM channel_tracks ct JOIN tracks g ON g.id = ct.track_id "
            "WHERE COALESCE(g.canonical_track_id, g.id) = r.id AND ct.channel_id = ANY(:chans))"
        )
        params["chans"] = rules.channel_ids
    if rules.language:
        where.append("r.language = :lang")
        params["lang"] = rules.language
    if rules.min_duration is not None:
        where.append("r.duration >= :dmin")
        params["dmin"] = rules.min_duration
    if rules.max_duration is not None:
        where.append("r.duration <= :dmax")
        params["dmax"] = rules.max_duration
    if rules.year_from is not None:
        where.append("r.year >= :yfrom")
        params["yfrom"] = rules.year_from
    if rules.year_to is not None:
        where.append("r.year <= :yto")
        params["yto"] = rules.year_to
    if rules.not_played_days is not None:
        where.append("(mine.last IS NULL OR mine.last < now() - :quiet)")
        params["quiet"] = timedelta(days=rules.not_played_days)
    if rules.min_my_plays is not None:
        where.append("COALESCE(mine.n, 0) >= :minplays")
        params["minplays"] = rules.min_my_plays

    sql = (
        f"WITH src AS ({_SOURCES[rules.source]}), "  # noqa: S608 - fixed fragments only
        "mine AS (SELECT track_id, count(*) AS n, max(played_at) AS last FROM play_history "
        "WHERE user_id = :uid AND played_at > now() - :hist GROUP BY track_id) "
        "SELECT r.id, count(*) OVER () AS total "
        "FROM src JOIN tracks r ON r.id = src.tid "
        "LEFT JOIN mine ON mine.track_id = r.id "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {_ORDER[rules.sort]} LIMIT :lim"
    )
    return sql, params


async def matching(session: AsyncSession, user_id: int, rules: SmartRules) -> tuple[list[int], int]:
    """The ids the rules pick right now, in order, and how many songs match in all."""
    sql, params = _query(rules, user_id)
    rows = (await session.execute(text(sql).bindparams(**params))).all()
    total = int(rows[0].total) if rows else 0
    return [int(row.id) for row in rows], total


async def rebuild(session: AsyncSession, playlist: Playlist) -> None:
    """Rewrites the playlist's songs from its rules."""
    assert playlist.kind == KIND
    assert playlist.rules is not None
    rules = SmartRules.model_validate(playlist.rules)
    ids, _ = await matching(session, playlist.user_id, rules)
    await session.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist.id))
    if ids:
        await session.execute(
            insert(PlaylistTrack).values(
                [
                    {
                        "playlist_id": playlist.id,
                        "track_id": tid,
                        "position": playlists.STEP * (i + 1),
                        "added_by": playlist.user_id,
                    }
                    for i, tid in enumerate(ids)
                ]
            )
        )
    await playlists.refresh_counts(session, playlist.id)
    now = datetime.now(UTC)
    await session.execute(
        update(Playlist).where(Playlist.id == playlist.id).values(refreshed_at=now)
    )
    await session.refresh(playlist)


async def create(
    session: AsyncSession,
    user_id: int,
    name: str,
    description: str | None,
    rules: SmartRules,
) -> Playlist:
    _query(rules, user_id)  # reject contradictory rules before anything is written
    plan = await playlists._plan(session, user_id)
    count = await session.scalar(
        select(func.count())
        .select_from(Playlist)
        .where(Playlist.user_id == user_id, Playlist.kind.in_(("manual", KIND)))
    )
    if not plan.allows("playlists", count or 0):
        raise LimitReached(
            "playlist limit reached", kind="playlists", limit=plan.limit("playlists")
        )
    playlist = Playlist(
        user_id=user_id,
        name=name.strip()[:100],
        description=description,
        kind=KIND,
        rules=rules.model_dump(mode="json"),
    )
    session.add(playlist)
    await session.flush()
    await rebuild(session, playlist)
    return playlist


async def owned(session: AsyncSession, playlist_id: int, user_id: int) -> Playlist:
    found = await playlists.access(session, playlist_id, user_id)
    if found.playlist.user_id != user_id:
        raise Forbidden("only the owner can change a smart playlist")
    if found.playlist.kind != KIND:
        raise InvalidInput("not a smart playlist", reason="not_smart")
    return found.playlist


async def set_rules(
    session: AsyncSession, playlist_id: int, user_id: int, rules: SmartRules
) -> Playlist:
    playlist = await owned(session, playlist_id, user_id)
    _query(rules, user_id)
    playlist.rules = rules.model_dump(mode="json")
    await session.flush()
    await rebuild(session, playlist)
    return playlist


async def refresh_if_stale(session: AsyncSession, playlist: Playlist) -> bool:
    """Rebuilds a smart playlist that has not been rebuilt for a while. True if it did."""
    if playlist.kind != KIND or playlist.rules is None:
        return False
    if playlist.refreshed_at is not None and (
        datetime.now(UTC) - playlist.refreshed_at < STALE_AFTER
    ):
        return False
    await rebuild(session, playlist)
    return True


def is_smart(playlist: Playlist) -> bool:
    return playlist.kind == KIND
