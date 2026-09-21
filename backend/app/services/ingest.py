"""Turn raw audio messages into tracks. Set-based: a fixed number of queries per batch.

Identity (ADR-0005):
1. ``file_unique_id`` — exact. Same file in 50 channels = 1 track + 50 channel_tracks.
2. fuzzy — re-uploads get their own row pointing at the oldest match through
   ``canonical_track_id`` (same artist key, |Δduration| ≤ 2s, title trigram similarity).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert

from app import metrics
from app.domain.text.artist_parser import ParsedMeta, candidate_artist_names, parse_track_meta
from app.domain.text.language import detect_language
from app.domain.text.normalizer import normalize_key
from app.models import Artist, Blacklist, Channel, ChannelTrack, Track, TrackArtist
from app.services import plans
from tmusic_common.indexer_contract import AudioItem

MAX_ARTIST_NAME = 200


@dataclass(slots=True)
class IngestStats:
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    skipped: int = 0
    new_track_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ArtistHit:
    id: int
    key: str  # canonical normalized_name


async def _resolve_known(session: AsyncSession, keys: Iterable[str]) -> dict[str, _ArtistHit]:
    """Map any name/alias key to its (merge-resolved) artist."""
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(Artist.id, Artist.normalized_name, Artist.aliases, Artist.merged_into_id).where(
                Artist.normalized_name.in_(wanted) | Artist.aliases.overlap(wanted)
            )
        )
    ).all()
    targets = {r.merged_into_id for r in rows if r.merged_into_id is not None}
    merged: dict[int, _ArtistHit] = {}
    if targets:
        for tid, tkey in (
            await session.execute(
                select(Artist.id, Artist.normalized_name).where(Artist.id.in_(targets))
            )
        ).all():
            merged[tid] = _ArtistHit(tid, tkey)
    found: dict[str, _ArtistHit] = {}
    wanted_set = set(wanted)
    for r in rows:
        hit = (
            merged.get(r.merged_into_id)
            if r.merged_into_id
            else _ArtistHit(r.id, r.normalized_name)
        )
        if hit is None:
            continue
        if r.normalized_name in wanted_set:
            found[r.normalized_name] = hit  # an exact name beats someone else's alias
        for k in set(r.aliases) & wanted_set:
            found.setdefault(k, hit)
    return found


async def _ensure_artists(
    session: AsyncSession, names: dict[str, str], known: dict[str, _ArtistHit]
) -> dict[str, _ArtistHit]:
    """Create artists for keys not found; ``names`` maps key → display name."""
    unresolved = [k for k in names if k not in known]
    if unresolved:
        known = {**known, **await _resolve_known(session, unresolved)}
    missing = {k: v for k, v in names.items() if k not in known}
    if missing:
        await session.execute(
            insert(Artist)
            .values(
                [{"name": v[:MAX_ARTIST_NAME], "normalized_name": k} for k, v in missing.items()]
            )
            .on_conflict_do_nothing(index_elements=[Artist.normalized_name])
        )
        known = {**known, **await _resolve_known(session, missing)}
    return known


def _title_key(meta: ParsedMeta, item: AudioItem) -> str:
    return normalize_key(meta.title) or normalize_key(item.file_name)


def source_key(channel_id: int, message_id: int) -> str:
    """Identity of a crawled track before anyone has resolved a file id for it."""
    return f"{channel_id}:{message_id}"


def _identity(channel_id: int, item: AudioItem) -> str:
    """The key this item is known by, whichever path it arrived on."""
    return item.file_unique_id or source_key(channel_id, item.message_id)


async def ingest_items(
    session: AsyncSession,
    channel: Channel,
    items: Sequence[AudioItem],
    *,
    bot_id: int | None = None,
) -> IngestStats:
    stats = IngestStats()
    if not items:
        return stats

    # A crawled batch has no file ids yet; blacklisting still works on the ones it has.
    crawled = any(i.file_unique_id is None for i in items)
    fuids = sorted({i.file_unique_id for i in items if i.file_unique_id})
    banned = (
        set(
            (
                await session.scalars(
                    select(Blacklist.value).where(
                        Blacklist.entity_type == "track", Blacklist.value.in_(fuids)
                    )
                )
            ).all()
        )
        if fuids
        else set()
    )
    usable = [i for i in items if i.file_unique_id not in banned]
    stats.skipped = len(items) - len(usable)
    if not usable:
        return stats

    # Newest message wins when the same file appears twice in one batch.
    by_fuid: dict[str, AudioItem] = {}
    for item in sorted(usable, key=lambda i: i.message_id):
        by_fuid[_identity(channel.id, item)] = item

    channel_names = (channel.username, channel.title)
    candidates: set[str] = set()
    for item in by_fuid.values():
        candidates |= candidate_artist_names(item.title, item.file_name)
        candidates.add(normalize_key(item.performer))
    known = await _resolve_known(session, candidates)

    parsed: dict[str, ParsedMeta] = {
        fuid: parse_track_meta(
            item.title,
            item.performer,
            item.file_name,
            item.caption,
            channel_names=channel_names,
            known_artist=known.__contains__,
        )
        for fuid, item in by_fuid.items()
    }
    display: dict[str, str] = {}
    for meta in parsed.values():
        for name in (*meta.artists, *meta.features):
            key = normalize_key(name)
            if key and len(key) <= MAX_ARTIST_NAME:
                display.setdefault(key, name)
    known = await _ensure_artists(session, display, known)

    now = datetime.now(UTC)
    rows = []
    for fuid, item in by_fuid.items():
        meta = parsed[fuid]
        primary = next((known[k] for a in meta.artists if (k := normalize_key(a)) in known), None)
        rows.append(
            {
                "file_unique_id": item.file_unique_id,
                "source_key": None if item.file_unique_id else fuid,
                "cdn_url": item.cdn_url,
                "cdn_url_fetched_at": now if item.cdn_url else None,
                # A crawled track is playable from the CDN but has no file id yet.
                "resolve_status": "unresolved" if item.file_unique_id is None else "resolved",
                "resolved_at": None if item.file_unique_id is None else now,
                "bot_file_id": item.bot_file_id if bot_id else None,
                "bot_id": bot_id if item.bot_file_id else None,
                "bot_file_id_updated_at": now if (bot_id and item.bot_file_id) else None,
                "title": meta.title or (item.file_name or "")[:512],
                "performer": item.performer,
                "file_name": item.file_name,
                "duration": item.duration,
                "file_size": item.file_size,
                "mime_type": item.mime_type,
                "has_thumb": item.has_thumb,
                "language": detect_language(meta.title, *meta.artists),
                "normalized_title": _title_key(meta, item),
                "normalized_artist": primary.key if primary else "",
                "metadata_confidence": meta.confidence,
            }
        )

    track_insert = insert(Track).values(rows)
    # Both indexes are partial and mutually exclusive, so the batch picks the one that
    # matches the identity its items carry.
    conflict_target = [Track.source_key] if crawled else [Track.file_unique_id]
    conflict_where = Track.source_key.is_not(None) if crawled else Track.file_unique_id.is_not(None)
    upsert: ReturningInsert[Any] = track_insert.on_conflict_do_update(
        index_elements=conflict_target,
        index_where=conflict_where,
        set_={
            # Metadata is never overwritten (admins may have corrected it); only file refs.
            "bot_file_id": text("COALESCE(EXCLUDED.bot_file_id, tracks.bot_file_id)"),
            "bot_id": text("COALESCE(EXCLUDED.bot_id, tracks.bot_id)"),
            "bot_file_id_updated_at": text(
                "COALESCE(EXCLUDED.bot_file_id_updated_at, tracks.bot_file_id_updated_at)"
            ),
            "has_thumb": text("tracks.has_thumb OR EXCLUDED.has_thumb"),
            # A re-crawl brings a fresh CDN link; an expired one is worse than useless.
            "cdn_url": text("COALESCE(EXCLUDED.cdn_url, tracks.cdn_url)"),
            "cdn_url_fetched_at": text(
                "COALESCE(EXCLUDED.cdn_url_fetched_at, tracks.cdn_url_fetched_at)"
            ),
            "playable": True,
        },
    ).returning(
        Track.id,
        Track.file_unique_id,
        Track.source_key,
        literal_column("(xmax = 0)").label("inserted"),
    )
    track_ids: dict[str, int] = {}
    new_ids: list[int] = []
    for row in (await session.execute(upsert)).all():
        track_ids[row.file_unique_id or row.source_key] = row.id
        if row.inserted:
            new_ids.append(row.id)
    stats.inserted = len(new_ids)
    stats.updated = len(track_ids) - len(new_ids)
    stats.new_track_ids = new_ids

    new_set = set(new_ids)
    links = []
    for fuid, meta in parsed.items():
        tid = track_ids[fuid]
        if tid not in new_set:
            continue
        seen: set[int] = set()
        for role, names in (("primary", meta.artists), ("feature", meta.features)):
            for pos, name in enumerate(names):
                hit = known.get(normalize_key(name))
                if hit and hit.id not in seen:
                    seen.add(hit.id)
                    links.append(
                        {"track_id": tid, "artist_id": hit.id, "role": role, "position": pos}
                    )
    if links:
        await session.execute(insert(TrackArtist).values(links).on_conflict_do_nothing())
        await session.execute(
            text(
                "UPDATE artists a SET tracks_count = a.tracks_count + c.n "
                "FROM (SELECT artist_id, count(*) AS n "
                "FROM unnest(CAST(:aids AS bigint[])) AS artist_id GROUP BY artist_id) c "
                "WHERE a.id = c.artist_id"
            ).bindparams(bindparam("aids", [link["artist_id"] for link in links])),
        )

    ct_rows = [
        {
            "channel_id": channel.id,
            "message_id": i.message_id,
            "track_id": track_ids[_identity(channel.id, i)],
            "posted_at": i.posted_at,
            "views": i.views,
        }
        for i in {i.message_id: i for i in usable}.values()
    ]
    ct_insert = insert(ChannelTrack).values(ct_rows)
    ct: ReturningInsert[Any] = ct_insert.on_conflict_do_update(
        index_elements=[ChannelTrack.channel_id, ChannelTrack.message_id],
        set_={"track_id": ct_insert.excluded.track_id, "views": ct_insert.excluded.views},
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    new_links = sum(1 for r in (await session.execute(ct)).all() if r.inserted)
    if new_links:
        await session.execute(
            update(Channel)
            .where(Channel.id == channel.id)
            .values(tracks_count=Channel.tracks_count + new_links)
        )

    if new_ids:
        stats.duplicates = await dedup(session, new_ids)
    await refresh_group_counts(session, list(track_ids.values()))
    for result, count in (
        ("inserted", stats.inserted),
        ("updated", stats.updated),
        ("duplicate", stats.duplicates),
        ("skipped", stats.skipped),
    ):
        if count:
            metrics.INDEX_ITEMS.labels(result).inc(count)
    return stats


async def dedup(session: AsyncSession, ids: list[int]) -> int:
    threshold = float(await plans.get_flag(session, "dedup_threshold", 0.88))
    no_artist = float(await plans.get_flag(session, "dedup_threshold_no_artist", 0.95))
    matched = (
        await session.execute(
            text(
                """
                UPDATE tracks n SET canonical_track_id = m.cand
                FROM (
                    SELECT n2.id AS nid, c.id AS cand
                    FROM tracks n2
                    CROSS JOIN LATERAL (
                        SELECT t.id FROM tracks t
                        WHERE t.normalized_artist = n2.normalized_artist
                          AND t.duration BETWEEN n2.duration - 2 AND n2.duration + 2
                          AND t.id < n2.id
                          AND t.canonical_track_id IS NULL
                          AND similarity(t.normalized_title, n2.normalized_title) >=
                              CASE WHEN n2.normalized_artist = '' THEN :no_artist ELSE :th END
                        ORDER BY t.id
                        LIMIT 1
                    ) c
                    WHERE n2.id = ANY(:ids) AND n2.normalized_title <> ''
                ) m
                WHERE n.id = m.nid
                RETURNING n.id
                """
            ).bindparams(ids=ids, th=threshold, no_artist=no_artist)
        )
    ).all()
    if not matched:
        return 0
    # A new row may have matched another new row that itself got a canonical in the same
    # statement; point it at the root instead.
    await session.execute(
        text(
            "UPDATE tracks c SET canonical_track_id = p.canonical_track_id "
            "FROM tracks p WHERE c.canonical_track_id = p.id "
            "AND p.canonical_track_id IS NOT NULL AND c.id = ANY(:ids)"
        ).bindparams(ids=ids)
    )
    # Re-uploads of taken-down content stay hidden.
    await session.execute(
        text(
            "UPDATE tracks c SET hidden = true, hidden_reason = 'duplicate_of_hidden' "
            "FROM tracks p WHERE c.canonical_track_id = p.id AND p.hidden "
            "AND NOT c.hidden AND c.id = ANY(:ids)"
        ).bindparams(ids=ids)
    )
    return len(matched)


async def refresh_group_counts(session: AsyncSession, ids: list[int]) -> None:
    """channels_count on each affected root = distinct channels across its group."""
    await session.execute(
        text(
            """
            WITH roots AS (
                SELECT DISTINCT COALESCE(canonical_track_id, id) AS rid
                FROM tracks WHERE id = ANY(:ids)
            ), members AS (
                SELECT r.rid, g.id FROM roots r JOIN tracks g ON g.id = r.rid
                UNION ALL
                SELECT r.rid, g.id FROM roots r JOIN tracks g ON g.canonical_track_id = r.rid
            ), counts AS (
                SELECT m.rid, count(DISTINCT ct.channel_id) AS n
                FROM members m JOIN channel_tracks ct ON ct.track_id = m.id
                GROUP BY m.rid
            )
            UPDATE tracks t SET channels_count = counts.n
            FROM counts WHERE t.id = counts.rid AND t.channels_count <> counts.n
            """
        ).bindparams(ids=ids)
    )


async def set_artist(session: AsyncSession, track_ids: Sequence[int], name: str) -> None:
    """Replaces the primary artist of ``track_ids`` — the admin review queue's fix.

    The artist row is created if it does not exist yet, exactly as ingest would; the
    tracks' ``normalized_artist`` is updated too, because that column is what dedup
    and the library group by.
    """
    if not track_ids:
        return
    key = normalize_key(name)
    if not key:
        return
    known = await _ensure_artists(session, {key: name}, {})
    hit = known.get(key)
    if hit is None:
        return
    ids = list(track_ids)
    await session.execute(
        text(
            "DELETE FROM track_artists WHERE track_id = ANY(:ids) AND role = 'primary'"
        ).bindparams(ids=ids)
    )
    await session.execute(
        insert(TrackArtist)
        .values(
            [
                {"track_id": tid, "artist_id": hit.id, "role": "primary", "position": 0}
                for tid in ids
            ]
        )
        .on_conflict_do_nothing()
    )
    await session.execute(
        text(
            "UPDATE tracks SET normalized_artist = :key, metadata_confidence = 100"
            " WHERE id = ANY(:ids)"
        ).bindparams(key=key, ids=ids)
    )
    await session.execute(
        text(
            "UPDATE artists SET tracks_count = (SELECT count(*) FROM track_artists"
            "   WHERE artist_id = artists.id) WHERE id = :aid"
        ).bindparams(aid=hit.id)
    )
