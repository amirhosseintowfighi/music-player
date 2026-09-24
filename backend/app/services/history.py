"""Likes, play history and cross-device playback state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.errors import LimitReached, NotFound
from app.models import Like, PlaybackState, Track
from app.services import plans, users
from app.services.pagination import cursor_datetime, cursor_int, decode_cursor, encode_cursor

MIN_PLAY_SECONDS = 5


@dataclass(frozen=True, slots=True)
class PlayEvent:
    track_id: int
    duration_played: int
    completed: bool
    source: str
    source_id: int | None
    device: str | None


async def canonical_id(session: AsyncSession, track_id: int) -> int:
    row = (
        await session.execute(
            select(func.coalesce(Track.canonical_track_id, Track.id)).where(Track.id == track_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFound("track not found")
    return int(row[0])


async def likes_count(session: AsyncSession, track_id: int) -> int:
    """Like count of the canonical track (duplicates share one count)."""
    root = await canonical_id(session, track_id)
    count = await session.scalar(select(Track.likes_count).where(Track.id == root))
    return int(count or 0)


async def like(session: AsyncSession, user_id: int, track_id: int) -> bool:
    """Returns True when a like was added, False when it already existed.

    The free plan caps how much of the library a listener keeps. Counting only when
    the like is new would still need the count, so it is checked up front — and only
    for plans that actually have a ceiling.
    """
    root = await canonical_id(session, track_id)
    user = await users.get_user(session, user_id)
    plan = await plans.get_plan(session, users.effective_plan(user)) if user else None
    if plan is not None and plan.limit("library") != plans.UNLIMITED:
        kept = await session.scalar(
            select(func.count()).select_from(Like).where(Like.user_id == user_id)
        )
        already = await session.scalar(
            select(Like.track_id).where(Like.user_id == user_id, Like.track_id == root)
        )
        if already is None and not plan.allows("library", int(kept or 0)):
            raise LimitReached("library limit reached", kind="library", limit=plan.limit("library"))
    result = await session.execute(
        insert(Like)
        .values(user_id=user_id, track_id=root)
        .on_conflict_do_nothing()
        .returning(Like.track_id)
    )
    added = result.first() is not None
    if added:
        await session.execute(
            text("UPDATE tracks SET likes_count = likes_count + 1 WHERE id = :t").bindparams(t=root)
        )
    return added


async def unlike(session: AsyncSession, user_id: int, track_id: int) -> bool:
    root = await canonical_id(session, track_id)
    result = await session.execute(
        delete(Like).where(Like.user_id == user_id, Like.track_id == root).returning(Like.track_id)
    )
    removed = result.first() is not None
    if removed:
        await session.execute(
            text(
                "UPDATE tracks SET likes_count = greatest(likes_count - 1, 0) WHERE id = :t"
            ).bindparams(t=root)
        )
    return removed


async def liked_ids(session: AsyncSession, user_id: int, track_ids: list[int]) -> set[int]:
    if not track_ids:
        return set()
    rows = await session.scalars(
        select(Like.track_id).where(Like.user_id == user_id, Like.track_id.in_(track_ids))
    )
    return set(rows.all())


async def liked_page(
    session: AsyncSession, user_id: int, cursor: str | None, limit: int
) -> tuple[list[int], str | None]:
    after = decode_cursor(cursor, 2)
    params: dict[str, object] = {"uid": user_id, "lim": limit + 1}
    where = ""
    if after is not None:
        where = " AND (l.created_at, l.track_id) < (:c_at, :c_id)"
        params |= {"c_at": cursor_datetime(after[0]), "c_id": cursor_int(after[1])}
    rows = (
        await session.execute(
            text(
                "SELECT l.track_id, l.created_at FROM likes l JOIN tracks t ON t.id = l.track_id "
                "WHERE l.user_id = :uid AND NOT t.hidden"
                + where
                + " ORDER BY l.created_at DESC, l.track_id DESC LIMIT :lim"
            ).bindparams(**params)
        )
    ).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].track_id)
    return [row.track_id for row in rows], next_cursor


async def record_play(session: AsyncSession, user_id: int, event: PlayEvent) -> bool:
    """Writes one play to the (partitioned) history. Very short plays are ignored."""
    if event.duration_played < MIN_PLAY_SECONDS:
        return False
    root = await canonical_id(session, event.track_id)
    await session.execute(
        text(
            "INSERT INTO play_history "
            "(user_id, track_id, played_at, duration_played, completed, skipped, source, source_id, device) "
            "VALUES (:uid, :tid, now(), :played, :completed, :skipped, :source, :source_id, :device)"
        ).bindparams(
            uid=user_id,
            tid=root,
            played=event.duration_played,
            completed=event.completed,
            skipped=not event.completed,
            source=event.source,
            source_id=event.source_id,
            device=(event.device or "")[:100] or None,
        )
    )
    metrics.PLAYS.labels(event.source).inc()
    await session.execute(
        text("UPDATE tracks SET plays_total = plays_total + 1 WHERE id = :t").bindparams(t=root)
    )
    return True


async def recent_ids(session: AsyncSession, user_id: int, limit: int) -> list[int]:
    """Distinct recently played tracks, newest first."""
    rows = await session.execute(
        text(
            "SELECT track_id, max(played_at) AS last FROM play_history "
            "WHERE user_id = :uid AND played_at > now() - interval '90 days' "
            "GROUP BY track_id ORDER BY last DESC LIMIT :lim"
        ).bindparams(uid=user_id, lim=limit)
    )
    return [row.track_id for row in rows]


async def save_playback(
    session: AsyncSession,
    user_id: int,
    *,
    track_id: int | None,
    position_s: int,
    queue: list[int],
    queue_index: int,
    shuffle: bool,
    repeat_mode: str,
    speed: float,
) -> None:
    """Upserts the resume point so another device can pick playback up."""
    values = {
        "user_id": user_id,
        "track_id": track_id,
        "position_s": max(0, position_s),
        "queue": queue[:200],
        "queue_index": max(0, queue_index),
        "shuffle": shuffle,
        "repeat_mode": repeat_mode if repeat_mode in ("off", "one", "all") else "off",
        "speed": min(max(speed, 0.5), 3.0),
        "updated_at": datetime.now(UTC),
    }
    await session.execute(
        insert(PlaybackState)
        .values(values)
        .on_conflict_do_update(index_elements=[PlaybackState.user_id], set_=values)
    )


async def load_playback(session: AsyncSession, user_id: int) -> PlaybackState | None:
    return await session.get(PlaybackState, user_id)
