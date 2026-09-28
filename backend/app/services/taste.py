"""What a listener tells us about themselves without making a playlist.

- **Hide a song**: it leaves every generated playlist and recommendation they see.
  **Snooze** is the same for 30 days, then it may come back.
- **Exclude a playlist from the taste profile**: what is played from it (a sleep
  playlist, the kids' songs) shapes nothing recommended to its owner.
- **Private session**: for six hours nothing they play is recorded — not in history,
  not in friends' feeds, not in their recommendations.
- **Progress on long tracks**: a podcast episode or a two-hour set resumes where it
  was left, on any device.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound
from app.models import HiddenTrack, Track, TrackProgress, User
from app.services import history

PRIVATE_FOR = timedelta(hours=6)
# A track this long is listened to in sittings, so its position is worth keeping.
LONG_FORM_S = 10 * 60
# The last minute of an episode is credits: close enough to call it finished.
FINISHED_MARGIN_S = 60


async def _track(session: AsyncSession, track_id: int) -> Track:
    track = await session.get(Track, await history.canonical_id(session, track_id))
    if track is None or track.hidden:
        raise NotFound("track not found")
    return track


# ── hide ──


async def hide(
    session: AsyncSession, user_id: int, track_id: int, snooze_days: int | None = None
) -> datetime | None:
    """Hides a song for good, or snoozes it for ``snooze_days``. Returns when it is back.

    Hiding again replaces a snooze and the other way round: the latest choice wins.
    """
    track = await _track(session, track_id)
    until = datetime.now(UTC) + timedelta(days=snooze_days) if snooze_days else None
    await session.execute(
        insert(HiddenTrack)
        .values(user_id=user_id, track_id=track.id, until=until)
        .on_conflict_do_update(
            index_elements=[HiddenTrack.user_id, HiddenTrack.track_id],
            set_={"until": until, "created_at": text("now()")},
        )
    )
    return until


async def unhide(session: AsyncSession, user_id: int, track_id: int) -> None:
    root = await history.canonical_id(session, track_id)
    await session.execute(
        delete(HiddenTrack).where(HiddenTrack.user_id == user_id, HiddenTrack.track_id == root)
    )


def _still_hidden() -> ColumnElement[bool]:
    return or_(HiddenTrack.until.is_(None), HiddenTrack.until > func.now())


# The same rule for raw SQL: ``x`` is the hidden_tracks alias.
STILL_HIDDEN_SQL = "(x.until IS NULL OR x.until > now())"

# Raw-SQL filter for play_history rows (alias ``h``) that may shape recommendations:
# plays from a playlist its owner excluded from their taste profile do not.
COUNTS_FOR_TASTE_SQL = (
    "NOT (h.source = 'playlist' AND h.source_id IS NOT NULL AND EXISTS ("
    "SELECT 1 FROM playlists xp WHERE xp.id = h.source_id AND xp.exclude_from_taste))"
)


async def hidden_ids(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.scalars(
        select(HiddenTrack.track_id).where(HiddenTrack.user_id == user_id, _still_hidden())
    )
    return set(rows.all())


async def hidden(session: AsyncSession, user_id: int) -> list[tuple[int, datetime | None]]:
    """Every hidden or snoozed song with when it comes back (None: never)."""
    rows = await session.execute(
        select(HiddenTrack.track_id, HiddenTrack.until)
        .where(HiddenTrack.user_id == user_id, _still_hidden())
        .order_by(HiddenTrack.created_at.desc())
    )
    return [(row.track_id, row.until) for row in rows]


async def without_hidden(session: AsyncSession, user_id: int, ids: Sequence[int]) -> list[int]:
    hidden = await hidden_ids(session, user_id)
    return [i for i in ids if i not in hidden]


# ── private session ──


async def private_until(session: AsyncSession, user_id: int) -> datetime | None:
    until = await session.scalar(select(User.private_until).where(User.id == user_id))
    return until if until and until > datetime.now(UTC) else None


async def set_private(session: AsyncSession, user_id: int, on: bool) -> datetime | None:
    until = datetime.now(UTC) + PRIVATE_FOR if on else None
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    user.private_until = until
    return until


# ── long-form progress ──


async def save_progress(
    session: AsyncSession, user_id: int, track_id: int, position_s: int
) -> bool:
    """Remembers where a long track was left. Returns whether it counts as finished."""
    track = await _track(session, track_id)
    position = max(0, min(position_s, track.duration or position_s))
    finished = bool(track.duration) and position >= track.duration - FINISHED_MARGIN_S
    await session.execute(
        insert(TrackProgress)
        .values(user_id=user_id, track_id=track.id, position_s=position, finished=finished)
        .on_conflict_do_update(
            index_elements=[TrackProgress.user_id, TrackProgress.track_id],
            set_={"position_s": position, "finished": finished, "updated_at": text("now()")},
        )
    )
    return finished


async def progress_for(
    session: AsyncSession, user_id: int, track_ids: Sequence[int]
) -> dict[int, tuple[int, bool]]:
    if not track_ids:
        return {}
    rows = await session.execute(
        select(TrackProgress.track_id, TrackProgress.position_s, TrackProgress.finished).where(
            TrackProgress.user_id == user_id, TrackProgress.track_id.in_(set(track_ids))
        )
    )
    return {row.track_id: (row.position_s, row.finished) for row in rows}


async def in_progress(session: AsyncSession, user_id: int, limit: int = 20) -> list[int]:
    """Long tracks started and not finished, most recent first."""
    rows = await session.scalars(
        select(TrackProgress.track_id)
        .join(Track, Track.id == TrackProgress.track_id)
        .where(
            TrackProgress.user_id == user_id,
            TrackProgress.finished.is_(False),
            TrackProgress.position_s > 30,
            Track.hidden.is_(False),
        )
        .order_by(TrackProgress.updated_at.desc())
        .limit(limit)
    )
    return list(rows.all())
