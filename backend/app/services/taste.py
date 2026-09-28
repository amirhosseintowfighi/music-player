"""What a listener tells us about themselves without making a playlist.

- **Hide a song**: it leaves every generated playlist and recommendation they see.
- **Private session**: for six hours nothing they play is recorded — not in history,
  not in friends' feeds, not in their recommendations.
- **Progress on long tracks**: a podcast episode or a two-hour set resumes where it
  was left, on any device.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
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


async def hide(session: AsyncSession, user_id: int, track_id: int) -> None:
    track = await _track(session, track_id)
    await session.execute(
        insert(HiddenTrack).values(user_id=user_id, track_id=track.id).on_conflict_do_nothing()
    )


async def unhide(session: AsyncSession, user_id: int, track_id: int) -> None:
    root = await history.canonical_id(session, track_id)
    await session.execute(
        delete(HiddenTrack).where(HiddenTrack.user_id == user_id, HiddenTrack.track_id == root)
    )


async def hidden_ids(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.scalars(select(HiddenTrack.track_id).where(HiddenTrack.user_id == user_id))
    return set(rows.all())


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
