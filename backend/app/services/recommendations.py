"""Recommendations: item-based collaborative filtering, trending and generated mixes.

Everything here is computed by a nightly job and read back cheaply, because the
recommendation endpoints sit on the Home screen and must answer in well under the
200 ms budget.

**Why the similarity matrix is built in SQL** (ADR-0017, deviating from ADR-0011):
the classic item-item cosine over an implicit feedback matrix is a self-join of the
interaction table plus a count, which Postgres does next to the data. Pulling the
matrix into numpy/scipy would add ~80 MB of wheels to the core image and a copy of
the whole interaction set into worker memory, for the same numbers. The join is
bounded by capping how many interactions each user contributes.

Cold start is never empty: a user with no history gets trending plus the tracks of
the channels they just added.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound
from app.models import Playlist, User
from app.services import history, plans, playlists, users
from tmusic_common.logging import get_logger

log = get_logger(__name__)

Window = Literal["24h", "7d", "30d"]
TrendKind = Literal["plays", "most_added", "rising"]

WINDOW_INTERVAL: dict[Window, str] = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}

# How many interactions one user may contribute to the matrix. A user who played
# 20k tracks would otherwise dominate the self-join.
# ponytail: fixed cap; make it adaptive only if the nightly job stops fitting its window.
MAX_INTERACTIONS_PER_USER = 500
MIN_CO_OCCURRENCE = 2  # a pair seen by a single user is noise, not a signal
NEIGHBOURS_PER_TRACK = 50
TRENDING_SIZE = 100
DISCOVER_SIZE = 30
MIX_SIZE = 25
MIXES_PER_USER = 3
MAX_PER_ARTIST = 2  # diversity rule from ADR-0011


@dataclass(frozen=True, slots=True)
class Section:
    """One row on the Discover screen."""

    id: str
    kind: Literal["playlist", "tracks", "channels"]
    title_fa: str
    title_en: str
    playlist_id: int | None
    track_ids: list[int]


# ── the interaction matrix ────────────────────────────────────────────────────

# A play only counts when it was finished: a skip says the opposite of a like.
# Likes weigh more than plays, and being in the same playlist is the strongest signal
# a user can give without listening.
_INTERACTIONS = """
WITH raw AS (
    SELECT user_id, coalesce(t.canonical_track_id, h.track_id) AS track_id,
           1.0::real AS weight, max(h.played_at) AS at
      FROM play_history h
      JOIN tracks t ON t.id = h.track_id
     WHERE h.completed AND h.played_at > now() - interval '180 days'
     GROUP BY 1, 2
    UNION ALL
    SELECT l.user_id, coalesce(t.canonical_track_id, l.track_id), 2.0::real, l.created_at
      FROM likes l JOIN tracks t ON t.id = l.track_id
    UNION ALL
    SELECT p.user_id, coalesce(t.canonical_track_id, pt.track_id), 2.5::real, pt.added_at
      FROM playlist_tracks pt
      JOIN playlists p ON p.id = pt.playlist_id
      JOIN tracks t ON t.id = pt.track_id
     WHERE p.kind = 'manual'
),
scored AS (
    SELECT user_id, track_id, max(weight) AS weight, max(at) AS at
      FROM raw GROUP BY 1, 2
),
capped AS (
    SELECT user_id, track_id, weight,
           row_number() OVER (PARTITION BY user_id ORDER BY weight DESC, at DESC) AS rn
      FROM scored
)
SELECT user_id, track_id, weight FROM capped WHERE rn <= :cap
"""


async def rebuild_similarity(session: AsyncSession) -> int:
    """Recomputes ``track_similarity``. Returns the number of neighbour rows written.

    Cosine over the implicit-feedback matrix: ``co(i,j) / sqrt(norm(i) * norm(j))``,
    keeping the strongest neighbours of each track in both directions.
    """
    result = await session.execute(
        text(
            f"""
        WITH inter AS ({_INTERACTIONS}),
        norms AS (
            SELECT track_id, sqrt(sum(weight * weight)) AS norm
              FROM inter GROUP BY track_id
        ),
        pairs AS (
            SELECT a.track_id AS t1, b.track_id AS t2,
                   sum(a.weight * b.weight) AS dot, count(*) AS co
              FROM inter a
              JOIN inter b ON a.user_id = b.user_id AND a.track_id < b.track_id
             GROUP BY 1, 2
            HAVING count(*) >= :min_co
        ),
        mirrored AS (  -- 'both' is a reserved word in Postgres

            SELECT t1 AS track_id, t2 AS similar_id, dot FROM pairs
            UNION ALL
            SELECT t2, t1, dot FROM pairs
        ),
        scored AS (
            SELECT b.track_id, b.similar_id,
                   (b.dot / (n1.norm * n2.norm))::real AS score,
                   row_number() OVER (
                       PARTITION BY b.track_id ORDER BY b.dot / (n1.norm * n2.norm) DESC
                   ) AS rn
              FROM mirrored b
              JOIN norms n1 ON n1.track_id = b.track_id
              JOIN norms n2 ON n2.track_id = b.similar_id
             WHERE n1.norm > 0 AND n2.norm > 0
        ),
        fresh AS (
            SELECT track_id, similar_id, score FROM scored WHERE rn <= :neighbours
        ),
        cleared AS (
            DELETE FROM track_similarity WHERE true RETURNING 1
        )
        INSERT INTO track_similarity (track_id, similar_id, score)
        SELECT track_id, similar_id, score FROM fresh
        RETURNING 1
        """
        ).bindparams(
            cap=MAX_INTERACTIONS_PER_USER, min_co=MIN_CO_OCCURRENCE, neighbours=NEIGHBOURS_PER_TRACK
        )
    )
    written = len(result.all())
    log.info("recs.similarity_rebuilt", rows=written)
    return written


# ── trending ──────────────────────────────────────────────────────────────────


async def rebuild_trending(session: AsyncSession) -> int:
    """Refreshes every (window, kind) snapshot. Returns the rows written."""
    now = datetime.now(UTC)
    total = 0
    for window, interval in WINDOW_INTERVAL.items():
        total += await _snapshot_plays(session, window, interval, now)
        total += await _snapshot_rising(session, window, interval, now)
        total += await _snapshot_most_added(session, window, now)
    log.info("recs.trending_rebuilt", rows=total)
    return total


async def _write_snapshot(
    session: AsyncSession, window: str, kind: str, rows: list[tuple[int, float]], now: datetime
) -> int:
    await session.execute(
        text("DELETE FROM trending_snapshots WHERE time_window = :w AND kind = :k").bindparams(
            w=window, k=kind
        )
    )
    if not rows:
        return 0
    values = [
        {"w": window, "k": kind, "rank": rank, "track_id": track_id, "score": score, "at": now}
        for rank, (track_id, score) in enumerate(rows, start=1)
    ]
    await session.execute(
        text(
            "INSERT INTO trending_snapshots (time_window, kind, rank, track_id, score, computed_at)"
            " VALUES (:w, :k, :rank, :track_id, :score, :at)"
        ),
        values,
    )
    return len(values)


async def _snapshot_plays(session: AsyncSession, window: str, interval: str, now: datetime) -> int:
    rows = (
        await session.execute(
            text(
                f"""
            SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id,
                   count(DISTINCT h.user_id)::real AS score
              FROM play_history h
              JOIN tracks t ON t.id = h.track_id
             WHERE h.played_at > now() - interval '{interval}'
               AND NOT t.hidden
             GROUP BY 1
             ORDER BY 2 DESC, 1
             LIMIT :limit
            """
            ).bindparams(limit=TRENDING_SIZE)
        )
    ).all()
    return await _write_snapshot(session, window, "plays", [(r[0], r[1]) for r in rows], now)


async def _snapshot_rising(session: AsyncSession, window: str, interval: str, now: datetime) -> int:
    """Tracks whose plays grew the most against the previous window of the same size."""
    rows = (
        await session.execute(
            text(
                f"""
            WITH recent AS (
                SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id,
                       count(*)::real AS plays
                  FROM play_history h JOIN tracks t ON t.id = h.track_id
                 WHERE h.played_at > now() - interval '{interval}' AND NOT t.hidden
                 GROUP BY 1
            ),
            previous AS (
                SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id,
                       count(*)::real AS plays
                  FROM play_history h JOIN tracks t ON t.id = h.track_id
                 WHERE h.played_at <= now() - interval '{interval}'
                   AND h.played_at > now() - interval '{interval}' * 2
                 GROUP BY 1
            )
            SELECT r.track_id,
                   (r.plays / (coalesce(p.plays, 0) + 1))::real AS score
              FROM recent r LEFT JOIN previous p USING (track_id)
             WHERE r.plays >= 3
             ORDER BY 2 DESC, r.plays DESC, 1
             LIMIT :limit
            """
            ).bindparams(limit=TRENDING_SIZE)
        )
    ).all()
    return await _write_snapshot(session, window, "rising", [(r[0], r[1]) for r in rows], now)


async def _snapshot_most_added(session: AsyncSession, window: str, now: datetime) -> int:
    """ "Most added": how many channels carry the track — the brief's own metric."""
    rows = (
        await session.execute(
            text(
                """
            SELECT id, channels_count::real
              FROM tracks
             WHERE canonical_track_id IS NULL AND NOT hidden AND channels_count > 1
             ORDER BY channels_count DESC, id
             LIMIT :limit
            """
            ).bindparams(limit=TRENDING_SIZE)
        )
    ).all()
    return await _write_snapshot(session, window, "most_added", [(r[0], r[1]) for r in rows], now)


async def trending_ids(
    session: AsyncSession, window: Window = "7d", kind: TrendKind = "plays", limit: int = 30
) -> list[int]:
    rows = await session.execute(
        text(
            "SELECT track_id FROM trending_snapshots"
            " WHERE time_window = :w AND kind = :k ORDER BY rank LIMIT :limit"
        ).bindparams(w=window, k=kind, limit=limit)
    )
    found = [row[0] for row in rows]
    if found:
        return found
    # Before the first nightly run there is no snapshot; most-added needs no history.
    fallback = await session.execute(
        text(
            "SELECT id FROM tracks WHERE canonical_track_id IS NULL AND NOT hidden"
            " ORDER BY channels_count DESC, id LIMIT :limit"
        ).bindparams(limit=limit)
    )
    return [row[0] for row in fallback]


# ── similar and radio ─────────────────────────────────────────────────────────


async def similar_ids(session: AsyncSession, track_id: int, limit: int = 20) -> list[int]:
    canonical = await history.canonical_id(session, track_id)
    rows = await session.execute(
        text(
            "SELECT s.similar_id FROM track_similarity s"
            " JOIN tracks t ON t.id = s.similar_id"
            " WHERE s.track_id = :id AND NOT t.hidden"
            " ORDER BY s.score DESC LIMIT :limit"
        ).bindparams(id=canonical, limit=limit)
    )
    found = [row[0] for row in rows]
    if found:
        return found
    # No neighbours yet (new track, or the job has not run): fall back to the artist.
    same_artist = await session.execute(
        text(
            """
        SELECT t.id FROM tracks t
         WHERE NOT t.hidden AND t.canonical_track_id IS NULL AND t.id <> :id
           AND EXISTS (
               SELECT 1 FROM track_artists a
                WHERE a.track_id = t.id AND a.artist_id IN (
                    SELECT artist_id FROM track_artists WHERE track_id = :id
                )
           )
         ORDER BY t.plays_total DESC, t.id
         LIMIT :limit
        """
        ).bindparams(id=canonical, limit=limit)
    )
    return [row[0] for row in same_artist]


async def radio_ids(session: AsyncSession, seed_track_id: int, limit: int = 50) -> list[int]:
    """A station around one track: the seed, its neighbours, then their neighbours.

    Artist diversity is applied at the end so a station never turns into one album.
    """
    seed = await history.canonical_id(session, seed_track_id)
    first = await similar_ids(session, seed, limit)
    pool = [seed, *first]
    if len(pool) < limit and first:
        second = await session.execute(
            text(
                "SELECT s.similar_id, max(s.score) AS score FROM track_similarity s"
                " JOIN tracks t ON t.id = s.similar_id"
                " WHERE s.track_id = ANY(:seeds) AND NOT t.hidden"
                " AND NOT (s.similar_id = ANY(:known))"
                " GROUP BY 1 ORDER BY 2 DESC LIMIT :limit"
            ).bindparams(seeds=first[:10], known=pool, limit=limit - len(pool))
        )
        pool.extend(row[0] for row in second)
    ordered = await _diversify(session, pool, keep_first=True)
    return ordered[:limit]


async def _diversify(
    session: AsyncSession, track_ids: list[int], *, keep_first: bool = False
) -> list[int]:
    """Keeps at most ``MAX_PER_ARTIST`` tracks per primary artist, preserving order."""
    if not track_ids:
        return []
    rows = await session.execute(
        text(
            "SELECT track_id, min(artist_id) FROM track_artists"
            " WHERE track_id = ANY(:ids) AND role = 'primary' GROUP BY track_id"
        ).bindparams(ids=track_ids)
    )
    artist_of = {row[0]: row[1] for row in rows}
    seen: dict[int, int] = {}
    out: list[int] = []
    for index, track_id in enumerate(track_ids):
        if keep_first and index == 0:
            out.append(track_id)
            continue
        artist = artist_of.get(track_id)
        if artist is not None:
            if seen.get(artist, 0) >= MAX_PER_ARTIST:
                continue
            seen[artist] = seen.get(artist, 0) + 1
        out.append(track_id)
    return out


# ── generated playlists ───────────────────────────────────────────────────────


def week_start(today: date | None = None) -> date:
    day = today or datetime.now(UTC).date()
    return day - timedelta(days=day.weekday())  # Monday


async def _seed_tracks(session: AsyncSession, user_id: int, limit: int = 40) -> list[int]:
    """What the user recently engaged with — the input to their recommendations."""
    rows = await session.execute(
        text(
            """
        SELECT track_id FROM (
            SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id, max(h.played_at) AS at
              FROM play_history h JOIN tracks t ON t.id = h.track_id
             WHERE h.user_id = :uid AND h.completed
               AND h.played_at > now() - interval '60 days'
             GROUP BY 1
            UNION ALL
            SELECT coalesce(t.canonical_track_id, l.track_id), l.created_at
              FROM likes l JOIN tracks t ON t.id = l.track_id
             WHERE l.user_id = :uid
        ) s
        GROUP BY track_id ORDER BY max(at) DESC LIMIT :limit
        """
        ).bindparams(uid=user_id, limit=limit)
    )
    return [row[0] for row in rows]


async def _candidates(
    session: AsyncSession, user_id: int, seeds: list[int], limit: int
) -> list[int]:
    """Neighbours of the seeds that the user has not heard, inside their library."""
    if not seeds:
        return []
    rows = await session.execute(
        text(
            """
        SELECT s.similar_id, sum(s.score) AS score
          FROM track_similarity s
          JOIN tracks t ON t.id = s.similar_id
         WHERE s.track_id = ANY(:seeds)
           AND NOT t.hidden AND t.canonical_track_id IS NULL
           AND NOT (s.similar_id = ANY(:seeds))
           AND EXISTS (
               SELECT 1 FROM channel_tracks tc
                JOIN user_channels uc ON uc.channel_id = tc.channel_id
               WHERE tc.track_id = t.id AND uc.user_id = :uid
           )
           AND NOT EXISTS (
               SELECT 1 FROM play_history h
                WHERE h.user_id = :uid AND h.track_id = s.similar_id
                  AND h.played_at > now() - interval '30 days'
           )
         GROUP BY 1
         ORDER BY 2 DESC
         LIMIT :limit
        """
        ).bindparams(seeds=seeds, uid=user_id, limit=limit * 3)
    )
    return [row[0] for row in rows]


async def _library_fallback(session: AsyncSession, user_id: int, limit: int) -> list[int]:
    """Cold start: popular tracks from the channels the user just added."""
    rows = await session.execute(
        text(
            """
        SELECT DISTINCT ON (t.id) t.id, t.channels_count
          FROM tracks t
          JOIN channel_tracks tc ON tc.track_id = t.id
          JOIN user_channels uc ON uc.channel_id = tc.channel_id
         WHERE uc.user_id = :uid AND NOT t.hidden AND t.canonical_track_id IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM play_history h
                WHERE h.user_id = :uid AND h.track_id = t.id
           )
         ORDER BY t.id, t.channels_count DESC
         LIMIT :limit
        """
        ).bindparams(uid=user_id, limit=limit * 4)
    )
    return [row[0] for row in rows]


async def _replace_generated(
    session: AsyncSession,
    user_id: int,
    *,
    kind: str,
    name: str,
    description: str | None,
    generated_for: date,
    track_ids: list[int],
) -> Playlist:
    """Creates (or refills) one generated playlist. Idempotent per (user, kind, day)."""
    existing = (
        await session.scalars(
            select(Playlist).where(
                Playlist.user_id == user_id,
                Playlist.kind == kind,
                Playlist.generated_for == generated_for,
                Playlist.name == name,
            )
        )
    ).first()
    playlist = existing
    if playlist is None:
        playlist = Playlist(
            user_id=user_id,
            name=name,
            description=description,
            kind=kind,
            generated_for=generated_for,
        )
        session.add(playlist)
        await session.flush()
    else:
        await session.execute(
            text("DELETE FROM playlist_tracks WHERE playlist_id = :id").bindparams(id=playlist.id)
        )

    if track_ids:
        await session.execute(
            text(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position)"
                " VALUES (:pid, :tid, :pos) ON CONFLICT DO NOTHING"
            ),
            [
                {"pid": playlist.id, "tid": track_id, "pos": index * 1024}
                for index, track_id in enumerate(track_ids, start=1)
            ],
        )
    await playlists.refresh_counts(session, playlist.id)
    # refresh_counts is a raw UPDATE, so the ORM copy still holds the old counters.
    await session.refresh(playlist)
    return playlist


async def _recently_played(session: AsyncSession, user_id: int, days: int = 30) -> set[int]:
    rows = await session.execute(
        text(
            "SELECT DISTINCT coalesce(t.canonical_track_id, h.track_id) FROM play_history h"
            " JOIN tracks t ON t.id = h.track_id"
            " WHERE h.user_id = :uid AND h.played_at > now() - make_interval(days => :days)"
        ).bindparams(uid=user_id, days=days)
    )
    return {row[0] for row in rows}


async def discover_weekly(
    session: AsyncSession, user_id: int, *, for_week: date | None = None
) -> Playlist:
    """The user's weekly mix. Re-running in the same week rebuilds the same playlist."""
    monday = for_week or week_start()
    seeds = await _seed_tracks(session, user_id)
    candidates = await _candidates(session, user_id, seeds, DISCOVER_SIZE)
    if len(candidates) < DISCOVER_SIZE:
        known = set(candidates)
        candidates += [
            track_id
            for track_id in await _library_fallback(session, user_id, DISCOVER_SIZE)
            if track_id not in known
        ]
    if len(candidates) < DISCOVER_SIZE:
        known = set(candidates)
        candidates += [
            track_id
            for track_id in await trending_ids(session, "7d", "plays", DISCOVER_SIZE)
            if track_id not in known
        ]
    # Whatever the fallback added, "discover" must not hand back this month's plays.
    heard = await _recently_played(session, user_id)
    candidates = [track_id for track_id in candidates if track_id not in heard]
    chosen = (await _diversify(session, candidates))[:DISCOVER_SIZE]
    return await _replace_generated(
        session,
        user_id,
        kind="discover_weekly",
        name="Discover Weekly",
        description=None,
        generated_for=monday,
        track_ids=chosen,
    )


async def daily_mixes(
    session: AsyncSession, user_id: int, *, for_day: date | None = None
) -> list[Playlist]:
    """Up to three mixes, each built around one artist the user actually listens to."""
    day = for_day or datetime.now(UTC).date()
    rows = await session.execute(
        text(
            """
        SELECT a.id, a.name, count(*) AS weight
          FROM play_history h
          JOIN track_artists ta ON ta.track_id = h.track_id AND ta.role = 'primary'
          JOIN artists a ON a.id = ta.artist_id
         WHERE h.user_id = :uid AND h.played_at > now() - interval '90 days'
         GROUP BY 1, 2 ORDER BY 3 DESC LIMIT :n
        """
        ).bindparams(uid=user_id, n=MIXES_PER_USER)
    )
    top_artists = [(row[0], row[1]) for row in rows]
    out: list[Playlist] = []
    for index, (artist_id, artist_name) in enumerate(top_artists, start=1):
        track_ids = await _mix_for_artist(session, user_id, artist_id)
        if len(track_ids) < 5:
            continue
        out.append(
            await _replace_generated(
                session,
                user_id,
                kind="daily_mix",
                name=f"Daily Mix {index}",
                description=artist_name,
                generated_for=day,
                track_ids=track_ids,
            )
        )
    return out


async def _mix_for_artist(session: AsyncSession, user_id: int, artist_id: int) -> list[int]:
    rows = await session.execute(
        text(
            """
        SELECT t.id
          FROM tracks t
          JOIN track_artists ta ON ta.track_id = t.id
          JOIN channel_tracks tc ON tc.track_id = t.id
          JOIN user_channels uc ON uc.channel_id = tc.channel_id
         WHERE uc.user_id = :uid AND ta.artist_id = :artist
           AND NOT t.hidden AND t.canonical_track_id IS NULL
         GROUP BY t.id
         ORDER BY t.plays_total DESC, t.id
         LIMIT :limit
        """
        ).bindparams(uid=user_id, artist=artist_id, limit=MIX_SIZE)
    )
    seed_pool = [row[0] for row in rows]
    if not seed_pool:
        return []
    neighbours = await session.execute(
        text(
            """
        SELECT s.similar_id, sum(s.score) AS score
          FROM track_similarity s
          JOIN tracks t ON t.id = s.similar_id
         WHERE s.track_id = ANY(:seeds) AND NOT t.hidden
           AND NOT (s.similar_id = ANY(:seeds))
           AND EXISTS (
               SELECT 1 FROM channel_tracks tc
                JOIN user_channels uc ON uc.channel_id = tc.channel_id
               WHERE tc.track_id = t.id AND uc.user_id = :uid
           )
         GROUP BY 1 ORDER BY 2 DESC LIMIT :limit
        """
        ).bindparams(seeds=seed_pool, uid=user_id, limit=MIX_SIZE)
    )
    # The anchor artist is the point of a Daily Mix, so the diversity cap applies only
    # to what surrounds them — otherwise the mix collapses to two tracks.
    anchor = seed_pool[: max(MIX_SIZE // 3, 1)]
    around = await _diversify(session, [row[0] for row in neighbours])
    return (anchor + around)[:MIX_SIZE]


# ── the Discover screen ───────────────────────────────────────────────────────


async def generated_playlists(session: AsyncSession, user_id: int) -> list[Playlist]:
    rows = await session.scalars(
        select(Playlist)
        .where(
            Playlist.user_id == user_id,
            Playlist.kind.in_(("discover_weekly", "daily_mix")),
            Playlist.tracks_count > 0,
        )
        .order_by(Playlist.kind, Playlist.name)
    )
    return list(rows.all())


async def discover_sections(session: AsyncSession, user_id: int) -> list[Section]:
    """The Discover feed, honouring the plan: free gets one mix, pro gets them all."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    plan = await plans.get_plan(session, users.effective_plan(user))
    all_mixes = "all_mixes" in plan.features

    sections: list[Section] = []
    for playlist in await generated_playlists(session, user_id):
        if playlist.kind == "daily_mix" and not all_mixes:
            continue
        sections.append(
            Section(
                id=f"pl-{playlist.id}",
                kind="playlist",
                title_fa="کشف هفتگی" if playlist.kind == "discover_weekly" else playlist.name,
                title_en="Discover Weekly" if playlist.kind == "discover_weekly" else playlist.name,
                playlist_id=playlist.id,
                track_ids=[],
            )
        )

    trending = await trending_ids(session, "7d", "plays", 20)
    if trending:
        sections.append(
            Section(
                id="trending-7d",
                kind="tracks",
                title_fa="پرشنونده‌های این هفته",
                title_en="Trending this week",
                playlist_id=None,
                track_ids=trending,
            )
        )
    most_added = await trending_ids(session, "7d", "most_added", 20)
    if most_added:
        sections.append(
            Section(
                id="most-added",
                kind="tracks",
                title_fa="پرتکرار در کانال‌ها",
                title_en="Most added",
                playlist_id=None,
                track_ids=most_added,
            )
        )
    rising = await trending_ids(session, "24h", "rising", 20)
    if rising:
        sections.append(
            Section(
                id="rising-24h",
                kind="tracks",
                title_fa="در حال صعود",
                title_en="Rising now",
                playlist_id=None,
                track_ids=rising,
            )
        )
    return sections


async def refresh_for_user(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """Regenerates one user's mixes; used by the nightly job and by the admin panel."""
    weekly = await discover_weekly(session, user_id)
    mixes = await daily_mixes(session, user_id)
    return {"discover_weekly": weekly.tracks_count, "daily_mixes": len(mixes)}


async def active_user_ids(session: AsyncSession, days: int = 30, limit: int = 5000) -> list[int]:
    """Users worth spending nightly compute on: they opened the app recently."""
    rows = await session.execute(
        text(
            "SELECT id FROM users WHERE NOT is_banned"
            " AND last_seen_at > now() - make_interval(days => :days)"
            " ORDER BY last_seen_at DESC LIMIT :limit"
        ).bindparams(days=days, limit=limit)
    )
    return [row[0] for row in rows]


async def prune_generated(session: AsyncSession, keep_days: int = 21) -> int:
    """Old generated playlists are noise in the library; the user never asked for them."""
    result = await session.execute(
        text(
            "DELETE FROM playlists WHERE kind IN ('discover_weekly','daily_mix')"
            " AND generated_for < current_date - make_interval(days => :days) RETURNING 1"
        ).bindparams(days=keep_days)
    )
    return len(result.all())


__all__ = [
    "Section",
    "active_user_ids",
    "daily_mixes",
    "discover_sections",
    "discover_weekly",
    "generated_playlists",
    "prune_generated",
    "radio_ids",
    "rebuild_similarity",
    "rebuild_trending",
    "refresh_for_user",
    "similar_ids",
    "trending_ids",
    "week_start",
]
