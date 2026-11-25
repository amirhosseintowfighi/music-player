"""Wake-up alarms (0020): the bot wakes you with a song.

A Mini App cannot run while it is closed, and a web page cannot ring on its own.
What reaches a sleeping phone is a Telegram message: at the chosen minute the bot
sends one, with a button that opens the app on ``#/wake/<id>``, which starts the
alarm's music with a gentle fade-in.

Times are the listener's own: a minute of the day plus the UTC offset it was set
in. ``next_fire_at`` is the UTC instant the worker looks for; it is computed when
the alarm is saved and again every time it rings. An alarm with no days rings once
and switches itself off.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInput, LimitReached, NotFound
from app.models import Alarm, Playlist
from app.services import notifications
from tmusic_common.logging import get_logger

log = get_logger(__name__)

MAX_ALARMS = 5
ALL_DAYS = 127
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def next_fire(
    minute_of_day: int, days: int, tz_offset_minutes: int, after: datetime
) -> datetime | None:
    """The first instant strictly after ``after`` at which the alarm rings, in UTC.

    ``days`` is a bit per weekday, Monday first; 0 means "the next time that minute
    comes round", once.
    """
    offset = timedelta(minutes=tz_offset_minutes)
    local_after = after.astimezone(UTC) + offset
    midnight = local_after.replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(8):
        local = midnight + timedelta(days=day, minutes=minute_of_day)
        if local <= local_after:
            continue
        if days and not days & (1 << local.weekday()):
            continue
        return (local - offset).replace(tzinfo=UTC)
    return None


async def _check_playlist(
    session: AsyncSession, user_id: int, source: str, playlist_id: int | None
) -> int | None:
    if source != "playlist":
        return None
    if playlist_id is None:
        raise InvalidInput("a playlist alarm needs a playlist", reason="no_playlist")
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or playlist.user_id != user_id:
        raise NotFound("playlist not found")
    return playlist_id


async def list_for(session: AsyncSession, user_id: int) -> list[Alarm]:
    rows = await session.scalars(
        select(Alarm).where(Alarm.user_id == user_id).order_by(Alarm.minute_of_day, Alarm.id)
    )
    return list(rows.all())


async def create(
    session: AsyncSession,
    user_id: int,
    *,
    minute_of_day: int,
    days: int,
    tz_offset_minutes: int,
    label: str | None,
    source: str,
    playlist_id: int | None,
) -> Alarm:
    count = await session.scalar(
        select(func.count()).select_from(Alarm).where(Alarm.user_id == user_id)
    )
    if (count or 0) >= MAX_ALARMS:
        raise LimitReached("alarm limit reached", kind="alarms", limit=MAX_ALARMS)
    alarm = Alarm(
        user_id=user_id,
        minute_of_day=minute_of_day,
        days=days,
        tz_offset_minutes=tz_offset_minutes,
        label=(label or "").strip()[:60] or None,
        source=source,
        playlist_id=await _check_playlist(session, user_id, source, playlist_id),
        enabled=True,
        next_fire_at=next_fire(minute_of_day, days, tz_offset_minutes, datetime.now(UTC)),
    )
    session.add(alarm)
    await session.flush()
    return alarm


async def get_owned(session: AsyncSession, alarm_id: int, user_id: int) -> Alarm:
    alarm = await session.get(Alarm, alarm_id)
    if alarm is None or alarm.user_id != user_id:
        raise NotFound("alarm not found")
    return alarm


async def update(
    session: AsyncSession,
    alarm: Alarm,
    *,
    minute_of_day: int | None = None,
    days: int | None = None,
    tz_offset_minutes: int | None = None,
    label: str | None = None,
    source: str | None = None,
    playlist_id: int | None = None,
    enabled: bool | None = None,
) -> Alarm:
    if minute_of_day is not None:
        alarm.minute_of_day = minute_of_day
    if days is not None:
        alarm.days = days
    if tz_offset_minutes is not None:
        alarm.tz_offset_minutes = tz_offset_minutes
    if label is not None:
        alarm.label = label.strip()[:60] or None
    if source is not None:
        alarm.source = source
        alarm.playlist_id = await _check_playlist(session, alarm.user_id, source, playlist_id)
    if enabled is not None:
        alarm.enabled = enabled
    alarm.next_fire_at = (
        next_fire(alarm.minute_of_day, alarm.days, alarm.tz_offset_minutes, datetime.now(UTC))
        if alarm.enabled
        else None
    )
    await session.flush()
    return alarm


async def ring_due(session: AsyncSession, now: datetime | None = None) -> int:
    """Queues a wake-up message for every alarm whose minute has come. Returns how many.

    Locks the due rows so two workers cannot both ring the same alarm; the dedup key
    makes a retried minute harmless as well.
    """
    now = now or datetime.now(UTC)
    due = (
        await session.scalars(
            select(Alarm)
            .where(Alarm.enabled.is_(True), Alarm.next_fire_at <= now)
            .order_by(Alarm.next_fire_at)
            .limit(500)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not due:
        return 0
    titles = await _first_titles(session, due)
    notices = []
    for alarm in due:
        fired_for = alarm.next_fire_at or now
        notices.append(
            notifications.Notice(
                user_id=alarm.user_id,
                kind="alarm",
                payload={
                    "alarm_id": alarm.id,
                    "label": alarm.label or "",
                    "title": titles.get(alarm.id, ""),
                    "open": f"wake/{alarm.id}",
                },
                dedup_key=f"alarm:{alarm.id}:{fired_for.isoformat()}",
            )
        )
        alarm.last_fired_at = now
        if alarm.days == 0:
            alarm.enabled = False
            alarm.next_fire_at = None
        else:
            alarm.next_fire_at = next_fire(
                alarm.minute_of_day, alarm.days, alarm.tz_offset_minutes, max(now, fired_for)
            )
    queued = await notifications.enqueue(session, notices)
    await session.flush()
    log.info("alarms.rang", due=len(due), queued=queued)
    return queued


async def _first_titles(session: AsyncSession, alarms: Sequence[Alarm]) -> dict[int, str]:
    """A song to name in the message, so it reads like a morning and not a reminder."""
    out: dict[int, str] = {}
    for alarm in alarms:
        ids = await wake_queue(session, alarm, limit=1)
        if ids:
            title = await session.scalar(
                text("SELECT title FROM tracks WHERE id = :t").bindparams(t=ids[0])
            )
            if title:
                out[alarm.id] = str(title)
    return out


async def wake_queue(session: AsyncSession, alarm: Alarm, limit: int = 30) -> list[int]:
    """What the alarm plays, best first: the client starts the queue with a fade-in."""
    uid = alarm.user_id
    if alarm.source == "playlist" and alarm.playlist_id is not None:
        sql = (
            "SELECT pt.track_id AS id FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
            "WHERE pt.playlist_id = :ref AND NOT t.hidden ORDER BY pt.position LIMIT :lim"
        )
        params: dict[str, object] = {"ref": alarm.playlist_id, "lim": limit}
    elif alarm.source == "recent":
        sql = (
            "SELECT track_id AS id FROM play_history WHERE user_id = :uid "
            "AND played_at > now() - interval '30 days' "
            "GROUP BY track_id ORDER BY count(*) DESC, max(played_at) DESC LIMIT :lim"
        )
        params = {"uid": uid, "lim": limit}
    elif alarm.source == "daylist":
        # The morning part of the listener's own history; falls back to likes below.
        sql = (
            "SELECT track_id AS id FROM play_history WHERE user_id = :uid "
            "AND played_at > now() - interval '60 days' "
            "AND extract(hour FROM played_at + make_interval(mins => :tz)) BETWEEN 5 AND 10 "
            "GROUP BY track_id ORDER BY count(*) DESC LIMIT :lim"
        )
        params = {"uid": uid, "lim": limit, "tz": alarm.tz_offset_minutes}
    else:
        sql = ""
        params = {}
    ids: list[int] = []
    if sql:
        ids = [int(r.id) for r in await session.execute(text(sql).bindparams(**params))]
    if not ids:
        rows = await session.execute(
            text(
                "SELECT l.track_id AS id FROM likes l JOIN tracks t ON t.id = l.track_id "
                "WHERE l.user_id = :uid AND NOT t.hidden ORDER BY random() LIMIT :lim"
            ).bindparams(uid=uid, lim=limit)
        )
        ids = [int(r.id) for r in rows]
    return ids
