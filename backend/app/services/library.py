"""Read side: the user's library and catalogue browsing.

Everything user-facing works on *root* tracks (``COALESCE(canonical_track_id, id)``),
so a song that exists in 50 channels shows up once.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import TextClause, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.text.normalizer import normalize_key
from app.errors import NotFound
from app.models import Artist, Track, TrackArtist
from app.schemas import AlbumOut, ArtistOut, ArtistRef, TrackChannelRef, TrackOut
from app.services.pagination import cursor_datetime, cursor_int, decode_cursor, encode_cursor

Lang = Literal["fa", "en"]


@dataclass(frozen=True, slots=True)
class TrackFilters:
    channel_id: int | None = None
    artist_id: int | None = None
    album: str | None = None
    language: str | None = None
    year: int | None = None
    min_duration: int | None = None
    max_duration: int | None = None


def filter_sql(f: TrackFilters, alias: str) -> tuple[str, dict[str, object]]:
    """Fixed SQL fragments with bound values only — never user text in the SQL."""
    parts: list[str] = []
    params: dict[str, object] = {}
    if f.artist_id is not None:
        parts.append(
            f"EXISTS (SELECT 1 FROM track_artists fa WHERE fa.track_id = {alias}.id "
            "AND fa.artist_id = :f_artist)"
        )
        params["f_artist"] = f.artist_id
    if f.album:
        parts.append(f"{alias}.normalized_album = :f_album")
        params["f_album"] = normalize_key(f.album)
    if f.language:
        parts.append(f"{alias}.language = :f_lang")
        params["f_lang"] = f.language
    if f.year is not None:
        parts.append(f"{alias}.year = :f_year")
        params["f_year"] = f.year
    if f.min_duration is not None:
        parts.append(f"{alias}.duration >= :f_min")
        params["f_min"] = f.min_duration
    if f.max_duration is not None:
        parts.append(f"{alias}.duration <= :f_max")
        params["f_max"] = f.max_duration
    return "".join(f" AND {p}" for p in parts), params


_ATTRIBUTION_SQL = text(
    """
    SELECT DISTINCT ON (root)
           COALESCE(g.canonical_track_id, g.id) AS root,
           c.id AS channel_id, c.username, c.title, c.is_featured, c.subscribers_count,
           (uc.user_id IS NOT NULL) AS joined
      FROM channel_tracks ct
      JOIN tracks g ON g.id = ct.track_id
      JOIN channels c ON c.id = ct.channel_id
                     AND c.username IS NOT NULL
                     AND c.status IN ('pending', 'indexing', 'active')
      LEFT JOIN user_channels uc ON uc.channel_id = c.id AND uc.user_id = :viewer
     WHERE COALESCE(g.canonical_track_id, g.id) = ANY(:ids)
     ORDER BY root,
              joined ASC,              -- a channel they have not joined is the point
              c.is_featured DESC,      -- then the shelf we already promote
              c.subscribers_count DESC,
              ct.posted_at ASC         -- and only then, whoever posted it first
    """
)


async def attribution(
    session: AsyncSession, ids: Sequence[int], viewer_id: int | None
) -> dict[int, TrackChannelRef]:
    """One channel per track to credit and link to (ADR-003 §2-5)."""
    if not ids:
        return {}
    rows = await session.execute(_ATTRIBUTION_SQL.bindparams(ids=list(ids), viewer=viewer_id or 0))
    return {
        int(row.root): TrackChannelRef(
            id=int(row.channel_id),
            username=row.username,
            title=row.title or (row.username or ""),
            is_featured=bool(row.is_featured),
            subscribers_count=int(row.subscribers_count or 0),
            joined=bool(row.joined),
        )
        for row in rows
    }


async def hydrate_tracks(
    session: AsyncSession, ids: Sequence[int], lang: Lang, viewer_id: int | None = None
) -> list[TrackOut]:
    """Load tracks + artists for ``ids`` in a fixed number of queries, keeping input order.

    With ``viewer_id`` the rows also carry that user's like state (one extra query).
    """
    if not ids:
        return []
    unique = list(dict.fromkeys(ids))
    tracks = {
        t.id: t for t in (await session.scalars(select(Track).where(Track.id.in_(unique)))).all()
    }
    artist_rows = await session.execute(
        select(TrackArtist.track_id, Artist.id, Artist.name, Artist.latin_name, TrackArtist.role)
        .join(Artist, Artist.id == TrackArtist.artist_id)
        .where(TrackArtist.track_id.in_(unique), Artist.hidden.is_(False))
        .order_by(TrackArtist.track_id, TrackArtist.role, TrackArtist.position)
    )
    artists: dict[int, list[ArtistRef]] = defaultdict(list)
    for track_id, artist_id, name, latin, role in artist_rows.tuples():
        shown = latin if (lang == "en" and latin) else name
        artists[track_id].append(ArtistRef(id=artist_id, name=shown, role=role))
    channels = await attribution(session, unique, viewer_id)
    liked: set[int] = set()
    if viewer_id is not None:
        from app.services.history import liked_ids

        liked = await liked_ids(session, viewer_id, unique)
    out: list[TrackOut] = []
    for tid in unique:
        t = tracks.get(tid)
        if t is None or t.hidden:
            continue
        out.append(
            TrackOut(
                id=t.id,
                title=t.title,
                artists=artists.get(t.id, []),
                album=t.album,
                duration=t.duration,
                language=t.language,
                year=t.year,
                has_thumb=t.has_thumb,
                channels_count=t.channels_count,
                playable=t.playable,
                liked=t.id in liked,
                channel=channels.get(t.id),
                palette=t.cover_palette,
            )
        )
    return out


async def get_track(
    session: AsyncSession, track_id: int, lang: Lang, viewer_id: int | None = None
) -> TrackOut:
    root = await session.scalar(
        select(Track.canonical_track_id).where(Track.id == track_id, Track.hidden.is_(False))
    )
    found = await hydrate_tracks(session, [root or track_id], lang, viewer_id)
    if not found:
        raise NotFound("track not found")
    return found[0]


_LIB_CTE = """
    WITH lib AS (
        SELECT COALESCE(t.canonical_track_id, t.id) AS tid, max(ct.posted_at) AS posted_at
        FROM user_channels uc
        JOIN channel_tracks ct ON ct.channel_id = uc.channel_id
        JOIN tracks t ON t.id = ct.track_id
        WHERE uc.user_id = :uid {channel}
        GROUP BY 1
    )
"""


def _lib_cte(f: TrackFilters) -> tuple[str, dict[str, object]]:
    if f.channel_id is None:
        return _LIB_CTE.format(channel=""), {}
    return _LIB_CTE.format(channel="AND uc.channel_id = :f_channel"), {"f_channel": f.channel_id}


async def library_tracks(
    session: AsyncSession,
    user_id: int,
    f: TrackFilters,
    cursor: str | None,
    limit: int,
    lang: Lang,
) -> tuple[list[TrackOut], str | None]:
    cte, cte_params = _lib_cte(f)
    where, params = filter_sql(f, "r")
    params |= cte_params | {"uid": user_id, "lim": limit + 1}
    after = decode_cursor(cursor, 2)
    if after is not None:
        where += " AND (lib.posted_at, lib.tid) < (:c_at, :c_id)"
        params |= {"c_at": cursor_datetime(after[0]), "c_id": cursor_int(after[1])}
    stmt: TextClause = text(
        cte
        + "SELECT lib.tid, lib.posted_at FROM lib JOIN tracks r ON r.id = lib.tid "
        + "WHERE NOT r.hidden"
        + where
        + " ORDER BY lib.posted_at DESC, lib.tid DESC LIMIT :lim"
    ).bindparams(**params)
    rows = (await session.execute(stmt)).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].posted_at, rows[-1].tid)
    return await hydrate_tracks(session, [r.tid for r in rows], lang, user_id), next_cursor


async def library_artists(
    session: AsyncSession, user_id: int, cursor: str | None, limit: int, lang: Lang
) -> tuple[list[ArtistOut], str | None]:
    cte, _ = _lib_cte(TrackFilters())
    params: dict[str, object] = {"uid": user_id, "lim": limit + 1}
    having = ""
    after = decode_cursor(cursor, 2)
    if after is not None:
        having = "HAVING (count(DISTINCT lib.tid), -a.id) < (:c_n, :c_neg_id)"
        params |= {"c_n": cursor_int(after[0]), "c_neg_id": -cursor_int(after[1])}
    rows = (
        await session.execute(
            text(
                cte + "SELECT a.id, a.name, a.latin_name, count(DISTINCT lib.tid) AS n FROM lib "
                "JOIN track_artists ta ON ta.track_id = lib.tid AND ta.role = 'primary' "
                "JOIN artists a ON a.id = ta.artist_id AND NOT a.hidden "
                "GROUP BY a.id " + having + " ORDER BY n DESC, a.id ASC LIMIT :lim"
            ).bindparams(**params)
        )
    ).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].n, rows[-1].id)
    items = [
        ArtistOut(
            id=r.id,
            name=r.latin_name if (lang == "en" and r.latin_name) else r.name,
            latin_name=r.latin_name,
            tracks_count=r.n,
        )
        for r in rows
    ]
    return items, next_cursor


async def library_albums(
    session: AsyncSession, user_id: int, limit: int, lang: Lang
) -> list[AlbumOut]:
    cte, _ = _lib_cte(TrackFilters())
    rows = (
        await session.execute(
            text(
                cte
                + """
                SELECT r.album, min(a.id) AS artist_id,
                       min(CASE WHEN :lang = 'en' THEN COALESCE(a.latin_name, a.name)
                                ELSE a.name END) AS artist_name,
                       count(*) AS n
                FROM lib JOIN tracks r ON r.id = lib.tid AND NOT r.hidden
                LEFT JOIN track_artists ta ON ta.track_id = r.id AND ta.role = 'primary'
                     AND ta.position = 0
                LEFT JOIN artists a ON a.id = ta.artist_id
                WHERE r.normalized_album IS NOT NULL AND r.normalized_album <> ''
                GROUP BY r.normalized_album, r.album
                ORDER BY n DESC, r.album
                LIMIT :lim
                """
            ).bindparams(uid=user_id, lim=limit, lang=lang)
        )
    ).all()
    return [
        AlbumOut(album=r.album, artist_id=r.artist_id, artist_name=r.artist_name, tracks_count=r.n)
        for r in rows
    ]


async def channel_tracks(
    session: AsyncSession,
    channel_id: int,
    cursor: str | None,
    limit: int,
    lang: Lang,
    viewer_id: int | None = None,
) -> tuple[list[TrackOut], str | None]:
    params: dict[str, object] = {"cid": channel_id, "lim": limit + 1}
    where = ""
    after = decode_cursor(cursor, 2)
    if after is not None:
        where = " AND (ct.posted_at, ct.message_id) < (:c_at, :c_mid)"
        params |= {"c_at": cursor_datetime(after[0]), "c_mid": cursor_int(after[1])}
    rows = (
        await session.execute(
            text(
                "SELECT COALESCE(t.canonical_track_id, t.id) AS tid, ct.posted_at, ct.message_id "
                "FROM channel_tracks ct JOIN tracks t ON t.id = ct.track_id "
                "WHERE ct.channel_id = :cid AND NOT t.hidden"
                + where
                + " ORDER BY ct.posted_at DESC, ct.message_id DESC LIMIT :lim"
            ).bindparams(**params)
        )
    ).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].posted_at, rows[-1].message_id)
    return await hydrate_tracks(session, [r.tid for r in rows], lang, viewer_id), next_cursor


async def artist_tracks(
    session: AsyncSession,
    artist_id: int,
    cursor: str | None,
    limit: int,
    lang: Lang,
    viewer_id: int | None = None,
) -> tuple[list[TrackOut], str | None]:
    params: dict[str, object] = {"aid": artist_id, "lim": limit + 1}
    where = ""
    after = decode_cursor(cursor, 2)
    if after is not None:
        where = " AND (t.channels_count, -t.id) < (:c_n, :c_neg_id)"
        params |= {"c_n": cursor_int(after[0]), "c_neg_id": -cursor_int(after[1])}
    rows = (
        await session.execute(
            text(
                "SELECT t.id, t.channels_count FROM track_artists ta "
                "JOIN tracks t ON t.id = ta.track_id "
                "WHERE ta.artist_id = :aid AND t.canonical_track_id IS NULL AND NOT t.hidden"
                + where
                + " ORDER BY t.channels_count DESC, t.id ASC LIMIT :lim"
            ).bindparams(**params)
        )
    ).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].channels_count, rows[-1].id)
    return await hydrate_tracks(session, [r.id for r in rows], lang, viewer_id), next_cursor


async def search_artists(
    session: AsyncSession, query: str, lang: Lang, limit: int = 8
) -> list[ArtistOut]:
    """Artists whose name matches what was typed, best known first.

    Postgres, not the search index: there are orders of magnitude fewer artists than
    tracks, they already have a trigram index, and a name is matched three ways —
    exactly, by trigram similarity, and by the Latin skeleton, so "moein", "Moein"
    and "معین" all find the same person.
    """
    key = normalize_key(query)
    if not key:
        return []
    rows = (
        await session.execute(
            text(
                """
        SELECT id, name, latin_name, tracks_count, image_url
          FROM artists
         WHERE merged_into_id IS NULL AND NOT hidden
           AND (normalized_name = :key
                OR normalized_name % :key
                OR lower(coalesce(latin_name, '')) LIKE :prefix
                -- A misspelling reaches a Persian name only through the Latin one:
                -- "Gogoosh" has no trigrams in common with "گوگوش".
                OR lower(coalesce(latin_name, '')) % :key
                OR :key = ANY(aliases))
         ORDER BY (normalized_name = :key) DESC,
                  greatest(
                      similarity(normalized_name, :key),
                      similarity(lower(coalesce(latin_name, '')), :key)
                  ) DESC,
                  tracks_count DESC
         LIMIT :lim
        """
            ).bindparams(key=key, prefix=f"{key.lower()}%", lim=limit)
        )
    ).mappings()
    return [
        ArtistOut(
            id=row["id"],
            name=(row["latin_name"] if (lang == "en" and row["latin_name"]) else row["name"]),
            latin_name=row["latin_name"],
            tracks_count=row["tracks_count"],
            image_url=row["image_url"],
        )
        for row in rows
    ]


async def artist_overview(
    session: AsyncSession,
    artist_id: int,
    lang: Lang,
    viewer_id: int | None = None,
    top: int = 10,
) -> tuple[list[TrackOut], list[dict[str, object]]]:
    """An artist page: the songs people actually like, then the albums.

    Popularity here is likes, not plays: a play can be an accident or a queue that
    kept going, a like is somebody saying so. Tracks with no album still belong in the
    top list — most of this catalogue arrives as singles.
    """
    root = await session.scalar(
        text("SELECT COALESCE(merged_into_id, id) FROM artists WHERE id = :aid").bindparams(
            aid=artist_id
        )
    )
    if root is None:
        raise NotFound("artist not found")

    popular = (
        await session.execute(
            text(
                """
        SELECT t.id
          FROM track_artists ta
          JOIN tracks t ON t.id = ta.track_id
         WHERE ta.artist_id = :aid AND t.canonical_track_id IS NULL AND NOT t.hidden
         ORDER BY t.likes_count DESC, t.channels_count DESC, t.id
         LIMIT :lim
        """
            ).bindparams(aid=root, lim=top)
        )
    ).all()

    albums = (
        await session.execute(
            text(
                """
        SELECT t.album AS name, min(t.year) AS year, count(*) AS tracks
          FROM track_artists ta
          JOIN tracks t ON t.id = ta.track_id
         WHERE ta.artist_id = :aid AND t.canonical_track_id IS NULL AND NOT t.hidden
           AND t.album IS NOT NULL AND t.album <> ''
         GROUP BY t.album
         ORDER BY year DESC NULLS LAST, tracks DESC
         LIMIT 50
        """
            ).bindparams(aid=root)
        )
    ).mappings()

    tracks = await hydrate_tracks(session, [r.id for r in popular], lang, viewer_id)
    return tracks, [dict(row) for row in albums]


async def get_artist(session: AsyncSession, artist_id: int, lang: Lang) -> ArtistOut:
    artist = await session.get(Artist, artist_id)
    if artist is not None and artist.merged_into_id is not None:
        artist = await session.get(Artist, artist.merged_into_id)
    if artist is None or artist.hidden:
        raise NotFound("artist not found")
    return ArtistOut(
        id=artist.id,
        name=artist.latin_name if (lang == "en" and artist.latin_name) else artist.name,
        latin_name=artist.latin_name,
        tracks_count=artist.tracks_count,
        image_url=artist.image_url,
    )


async def remember_palette(session: AsyncSession, track_id: int, colors: Sequence[str]) -> None:
    """Stores the artwork's dominant colours, once, for the canonical track."""
    await session.execute(
        text(
            "UPDATE tracks SET cover_palette = :palette"
            " WHERE id = (SELECT COALESCE(canonical_track_id, id) FROM tracks WHERE id = :id)"
            "   AND cover_palette IS NULL"
        ).bindparams(palette=",".join(colors), id=track_id)
    )
