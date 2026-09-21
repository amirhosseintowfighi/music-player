"""Track search: Meilisearch first, Postgres trigram fallback (ADR-0006)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.text.finglish import skeleton, to_latin
from app.domain.text.normalizer import normalize_key
from app.errors import Unavailable
from app.models import SearchHistory
from app.services.library import TrackFilters, filter_sql
from app.services.meili import PRIMARY_FIELDS, SKELETON_FIELDS, SKELETON_WEIGHT, MeiliClient
from tmusic_common.logging import get_logger

log = get_logger(__name__)

SYNC_WATERMARK_KEY = "search:sync:wm"
SYNC_LAG_S = 10
MIN_SKELETON_LEN = 2


@dataclass(frozen=True, slots=True)
class SearchHit:
    ids: list[int]
    total: int
    degraded: bool


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def meili_filters(channel_ids: list[int] | None, f: TrackFilters) -> list[str]:
    """Meilisearch filter expressions, built only from typed values."""
    out: list[str] = []
    if f.channel_id is not None:
        out.append(f"channel_ids = {int(f.channel_id)}")
    if channel_ids is not None:
        out.append(f"channel_ids IN [{', '.join(str(int(c)) for c in channel_ids)}]")
    if f.artist_id is not None:
        out.append(f"artist_ids = {int(f.artist_id)}")
    if f.language:
        out.append(f"language = {_quote(f.language)}")
    if f.year is not None:
        out.append(f"year = {int(f.year)}")
    if f.min_duration is not None:
        out.append(f"duration >= {int(f.min_duration)}")
    if f.max_duration is not None:
        out.append(f"duration <= {int(f.max_duration)}")
    if f.album:
        out.append(f"album_norm = {_quote(normalize_key(f.album))}")
    return out


def build_queries(q: str, filters: list[str]) -> list[dict[str, Any]]:
    norm = normalize_key(q)
    queries: list[dict[str, Any]] = [
        {"q": norm, "filter": filters, "attributesToSearchOn": PRIMARY_FIELDS}
    ]
    skel = skeleton(q)
    if len(skel.replace(" ", "")) >= MIN_SKELETON_LEN:
        queries.append(
            {
                "q": skel,
                "filter": filters,
                "attributesToSearchOn": SKELETON_FIELDS,
                "federationOptions": {"weight": SKELETON_WEIGHT},
            }
        )
    return queries


async def search_tracks(
    session: AsyncSession,
    meili: MeiliClient,
    q: str,
    *,
    channel_ids: list[int] | None,
    filters: TrackFilters,
    limit: int,
    offset: int,
) -> SearchHit:
    if channel_ids is not None and not channel_ids:
        return SearchHit([], 0, False)
    try:
        result = await meili.federated_search(
            build_queries(q, meili_filters(channel_ids, filters)), limit, offset
        )
        ids = [int(h["id"]) for h in result.get("hits", [])]
        total = int(result.get("estimatedTotalHits", len(ids)))
        return SearchHit(ids, total, False)
    except Unavailable:
        log.warning("search.fallback_to_postgres")
        return await _pg_search(session, q, channel_ids, filters, limit, offset)


async def _pg_search(
    session: AsyncSession,
    q: str,
    channel_ids: list[int] | None,
    f: TrackFilters,
    limit: int,
    offset: int,
) -> SearchHit:

    norm = normalize_key(q)
    if not norm:
        return SearchHit([], 0, True)
    where, params = filter_sql(f, "t")
    scope = channel_ids if f.channel_id is None else [f.channel_id]
    if scope is not None:
        where += (
            " AND EXISTS (SELECT 1 FROM tracks g JOIN channel_tracks ct ON ct.track_id = g.id "
            "WHERE (g.id = t.id OR g.canonical_track_id = t.id) AND ct.channel_id = ANY(:scope))"
        )
        params["scope"] = scope
    like = "%" + norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = (
        await session.execute(
            text(
                "SELECT t.id FROM tracks t "
                "WHERE t.canonical_track_id IS NULL AND NOT t.hidden "
                "AND (t.normalized_title % :q OR t.normalized_artist % :q "
                "     OR t.normalized_title LIKE :like OR t.normalized_artist LIKE :like "
                "     OR EXISTS (SELECT 1 FROM track_artists ta "
                "                JOIN artists a ON a.id = ta.artist_id "
                "                WHERE ta.track_id = t.id "
                "                  AND (:q = ANY(a.aliases) OR a.normalized_name % :q)))"
                + where
                + " ORDER BY greatest(similarity(t.normalized_title, :q), "
                "similarity(t.normalized_artist, :q)) DESC, t.channels_count DESC, t.id "
                "LIMIT :lim OFFSET :off"
            ).bindparams(q=norm, like=like, lim=limit, off=offset, **params)
        )
    ).all()
    ids = [r.id for r in rows]
    # Exact totals are too expensive here; report "at least what we have".
    return SearchHit(ids, offset + len(ids) + (1 if len(ids) == limit else 0), True)


async def record_history(session: AsyncSession, user_id: int, q: str, results: int) -> None:
    query = q.strip()[:200]
    if query:
        session.add(SearchHistory(user_id=user_id, query=query, results=results))


async def recent_history(session: AsyncSession, user_id: int, prefix: str, limit: int) -> list[str]:
    stmt = (
        select(SearchHistory.query)
        .where(SearchHistory.user_id == user_id)
        .order_by(SearchHistory.created_at.desc())
        .limit(50)
    )
    seen: list[str] = []
    key = normalize_key(prefix)
    for query in (await session.scalars(stmt)).all():
        if query not in seen and (not key or normalize_key(query).startswith(key)):
            seen.append(query)
        if len(seen) >= limit:
            break
    return seen


async def clear_history(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        text("DELETE FROM search_history WHERE user_id = :u").bindparams(u=user_id)
    )


# ── indexing into Meilisearch ──

_DOCS_SQL = text(
    """
    SELECT t.id, t.title, t.album, t.normalized_title, t.normalized_album, t.duration,
           t.language, t.year, t.channels_count, t.plays_7d,
           extract(epoch FROM t.created_at)::bigint AS created_at,
           COALESCE((
               SELECT array_agg(DISTINCT ct.channel_id)
               FROM tracks g
               JOIN channel_tracks ct ON ct.track_id = g.id
               JOIN channels c ON c.id = ct.channel_id AND c.status <> 'blacklisted'
               WHERE g.id = t.id OR g.canonical_track_id = t.id
           ), '{}') AS channel_ids,
           COALESCE((
               SELECT json_agg(json_build_object(
                   'id', a.id, 'key', a.normalized_name, 'name', a.name,
                   'latin', a.latin_name, 'aliases', a.aliases) ORDER BY ta.position)
               FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
               WHERE ta.track_id = t.id AND NOT a.hidden
           ), '[]') AS artists
    FROM tracks t
    WHERE t.id = ANY(:ids)
    """
)


def _document(row: Any) -> dict[str, Any]:
    raw_artists = row.artists if isinstance(row.artists, list) else json.loads(row.artists)
    names = [a["name"] for a in raw_artists]
    latin = [a["latin"] or to_latin(a["name"]) for a in raw_artists]
    aliases = [alias for a in raw_artists for alias in a["aliases"]]
    return {
        "id": row.id,
        "title_norm": row.normalized_title,
        "artists_norm": " ".join(a["key"] for a in raw_artists),
        "aliases": aliases,
        "album_norm": row.normalized_album or "",
        "title_latin": to_latin(row.title),
        "artists_latin": " ".join(latin),
        "title_skel": skeleton(row.title),
        "artists_skel": " ".join(skeleton(n) for n in (*names, *latin, *aliases)),
        "artist_ids": [a["id"] for a in raw_artists],
        "channel_ids": list(row.channel_ids),
        "language": row.language,
        "year": row.year,
        "duration": row.duration,
        "channels_count": row.channels_count,
        "plays_7d": row.plays_7d,
        "created_at": row.created_at,
    }


async def build_documents(session: AsyncSession, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    return [_document(r) for r in (await session.execute(_DOCS_SQL.bindparams(ids=ids))).all()]


async def sync_changes(
    session: AsyncSession,
    meili: MeiliClient,
    redis: Redis,
    batch: int = 1000,
    lag_s: int | None = None,
) -> int:
    """Push tracks changed since the watermark. Returns the number of rows processed.

    Rows younger than SYNC_LAG_S are left for the next run so a transaction that
    committed late (with an older ``now()``) is not skipped past.
    """
    raw = await redis.get(SYNC_WATERMARK_KEY)
    wm_ts, wm_id = raw.split("|", 1) if raw else ("1970-01-01T00:00:00+00:00", "0")
    rows = (
        await session.execute(
            text(
                "SELECT id, canonical_track_id, hidden, updated_at FROM tracks "
                "WHERE (updated_at, id) > (CAST(:ts AS timestamptz), :id) "
                "AND updated_at < now() - make_interval(secs => :lag) "
                "ORDER BY updated_at, id LIMIT :lim"
            ).bindparams(
                ts=wm_ts, id=int(wm_id), lag=SYNC_LAG_S if lag_s is None else lag_s, lim=batch
            )
        )
    ).all()
    if not rows:
        return 0
    live = [r.id for r in rows if r.canonical_track_id is None and not r.hidden]
    gone = [r.id for r in rows if r.canonical_track_id is not None or r.hidden]
    docs = await build_documents(session, live)
    if docs:
        await meili.add_documents(docs)
    if gone:
        await meili.delete_documents(gone)
    last = rows[-1]
    await redis.set(SYNC_WATERMARK_KEY, f"{last.updated_at.isoformat()}|{last.id}")
    return len(rows)


async def full_reindex(session: AsyncSession, meili: MeiliClient, redis: Redis) -> int:
    """Rebuild the index from Postgres (the source of truth)."""
    await meili.ensure_index()
    await meili.wait(await meili.delete_all())
    await redis.delete(SYNC_WATERMARK_KEY)
    started = await session.scalar(text("SELECT now() - interval '1 minute'"))
    total = 0
    while (n := await sync_changes(session, meili, redis, lag_s=0)) > 0:
        total += n
    # Rewind so the incremental sync re-checks rows written while we were rebuilding.
    await redis.set(SYNC_WATERMARK_KEY, f"{started.isoformat()}|0")
    return total
