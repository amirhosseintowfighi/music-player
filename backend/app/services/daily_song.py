"""The bot's song of the day, and the song /random picks.

Both choose from what the listener already has: first a song they liked and have
not played for a month (the pleasant "oh, this one"), then anything from their own
channels they have not heard lately, and only for someone with an empty library,
something popular. Hidden and snoozed songs never come up.

The song of the day is opt-in (/daily in the bot): a notification of its own kind,
sent late in the morning in the listener's time zone, with a button that opens the
app on the song.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import notifications
from tmusic_common.logging import get_logger

log = get_logger(__name__)

KIND = "daily_song"
SEND_HOUR = 11
BATCH = 2000

_NOT_HIDDEN = (
    "NOT r.hidden AND r.playable AND r.canonical_track_id IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM hidden_tracks h WHERE h.user_id = :uid AND h.track_id = r.id "
    "AND (h.until IS NULL OR h.until > now()))"
)
_QUIET = (
    "NOT EXISTS (SELECT 1 FROM play_history ph WHERE ph.user_id = :uid AND ph.track_id = r.id "
    "AND ph.played_at > now() - interval '30 days')"
)

# Fixed fragments only; the listener is a bound parameter.
_PICKS = (
    # A liked song not heard for a month.
    "SELECT r.id FROM likes l JOIN tracks r ON r.id = l.track_id "  # noqa: S608
    f"WHERE l.user_id = :uid AND {_NOT_HIDDEN} AND {_QUIET} ORDER BY random() LIMIT 1",
    # Anything from their own channels they have not heard lately.
    "SELECT r.id FROM user_channels uc "  # noqa: S608
    "JOIN channel_tracks ct ON ct.channel_id = uc.channel_id JOIN tracks t ON t.id = ct.track_id "
    "JOIN tracks r ON r.id = COALESCE(t.canonical_track_id, t.id) "
    f"WHERE uc.user_id = :uid AND {_NOT_HIDDEN} AND {_QUIET} ORDER BY random() LIMIT 1",
    # An empty library: something people are playing this week.
    "SELECT r.id FROM (SELECT id FROM tracks WHERE canonical_track_id IS NULL "  # noqa: S608
    "AND NOT hidden AND playable ORDER BY plays_7d DESC, plays_total DESC LIMIT 50) top "
    f"JOIN tracks r ON r.id = top.id WHERE {_NOT_HIDDEN} ORDER BY random() LIMIT 1",
)


async def pick(session: AsyncSession, user_id: int) -> int | None:
    for sql in _PICKS:
        found = await session.scalar(text(sql).bindparams(uid=user_id))
        if found is not None:
            return int(found)
    return None


async def set_enabled(session: AsyncSession, user_id: int, on: bool) -> None:
    await session.execute(
        text(
            "UPDATE users SET notification_prefs = "
            "COALESCE(notification_prefs, '{}'::jsonb) "
            "|| jsonb_build_object(CAST(:kind AS text), :on) "
            "WHERE id = :uid"
        ).bindparams(uid=user_id, kind=KIND, on=on)
    )


async def queue_due(session: AsyncSession, now: datetime | None = None) -> int:
    """Queues today's song for everyone who asked for it and whose late morning it is.

    Runs hourly; picks the people for whom it is now ``SEND_HOUR`` o'clock locally.
    The dedup key is the local date, so a second run in the same hour adds nothing.
    """
    now = now or datetime.now(UTC)
    rows = await session.execute(
        text(
            "SELECT id, tz_offset_minutes FROM users "
            "WHERE (notification_prefs ->> :kind)::boolean IS TRUE "
            "AND NOT bot_blocked AND NOT is_banned "
            "AND extract(hour FROM CAST(:now AS timestamptz) AT TIME ZONE 'UTC' "
            "  + make_interval(mins => tz_offset_minutes)) = :hour "
            "ORDER BY id LIMIT :lim"
        ).bindparams(kind=KIND, now=now, hour=SEND_HOUR, lim=BATCH)
    )
    notices = []
    for row in rows:
        track_id = await pick(session, int(row.id))
        if track_id is None:
            continue
        title = await session.scalar(
            text("SELECT title FROM tracks WHERE id = :t").bindparams(t=track_id)
        )
        local_day = (now + timedelta(minutes=int(row.tz_offset_minutes))).date()
        notices.append(
            notifications.Notice(
                user_id=int(row.id),
                kind=KIND,
                payload={"title": title or "", "open": f"track/{track_id}"},
                dedup_key=f"{KIND}:{local_day.isoformat()}",
            )
        )
    queued = await notifications.enqueue(session, notices)
    log.info("daily_song.queued", candidates=len(notices), queued=queued)
    return queued
