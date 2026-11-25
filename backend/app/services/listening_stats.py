"""Listening stats: the last week or month in numbers, for the stats screen.

Minutes are what was actually heard (``duration_played``), grouped by the
listener's own calendar days and hours. The period is compared with the one before
it, so "up 20%" means the same length of time, just earlier. Plays in a private
session were never recorded, so they are not here either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

TOP = 5


@dataclass(slots=True)
class Stats:
    days: int
    start: date
    minutes: int
    plays: int
    songs: int
    artists: int
    previous_minutes: int
    # One entry per local day in the period, oldest first.
    daily_minutes: list[int] = field(default_factory=list)
    # 24 buckets, local hours.
    hourly_plays: list[int] = field(default_factory=list)
    top_track_ids: list[tuple[int, int]] = field(default_factory=list)
    top_artists: list[tuple[int, str, int]] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)


async def for_user(
    session: AsyncSession,
    user_id: int,
    days: int,
    now: datetime | None = None,
    lang: str = "fa",
) -> Stats:
    now = now or datetime.now(UTC)
    tz = await session.scalar(select(User.tz_offset_minutes).where(User.id == user_id))
    offset = timedelta(minutes=int(tz if tz is not None else 210))
    today = (now + offset).date()
    start = today - timedelta(days=days - 1)
    # The UTC instants where the listener's period (and the one before) begin.
    since = datetime.combine(start, datetime.min.time(), UTC) - offset
    before = since - timedelta(days=days)
    params = {"uid": user_id, "since": since, "tz": int(offset.total_seconds() // 60)}

    totals = (
        await session.execute(
            text(
                "SELECT COALESCE(sum(duration_played), 0) AS secs, count(*) AS plays, "
                "count(DISTINCT track_id) AS songs FROM play_history "
                "WHERE user_id = :uid AND played_at >= :since"
            ).bindparams(uid=user_id, since=since)
        )
    ).one()
    previous = await session.scalar(
        text(
            "SELECT COALESCE(sum(duration_played), 0) FROM play_history "
            "WHERE user_id = :uid AND played_at >= :before AND played_at < :since"
        ).bindparams(uid=user_id, before=before, since=since)
    )
    daily_rows = await session.execute(
        text(
            "SELECT (played_at + make_interval(mins => :tz))::date AS day, "
            "sum(duration_played) AS secs FROM play_history "
            "WHERE user_id = :uid AND played_at >= :since GROUP BY 1"
        ).bindparams(**params)
    )
    by_day = {row.day: int(row.secs) for row in daily_rows}
    hourly_rows = await session.execute(
        text(
            "SELECT extract(hour FROM played_at + make_interval(mins => :tz))::int AS hour, "
            "count(*) AS n FROM play_history "
            "WHERE user_id = :uid AND played_at >= :since GROUP BY 1"
        ).bindparams(**params)
    )
    hourly = [0] * 24
    for row in hourly_rows:
        hourly[int(row.hour)] = int(row.n)
    top_tracks = await session.execute(
        text(
            "SELECT track_id, count(*) AS n FROM play_history "
            "WHERE user_id = :uid AND played_at >= :since "
            "GROUP BY track_id ORDER BY n DESC, max(played_at) DESC LIMIT :top"
        ).bindparams(uid=user_id, since=since, top=TOP)
    )
    top_artists = await session.execute(
        text(
            "SELECT a.id, a.name, a.latin_name, count(*) AS n FROM play_history ph "
            "JOIN track_artists ta ON ta.track_id = ph.track_id AND ta.role = 'primary' "
            "JOIN artists a ON a.id = ta.artist_id AND NOT a.hidden "
            "WHERE ph.user_id = :uid AND ph.played_at >= :since "
            "GROUP BY a.id, a.name, a.latin_name ORDER BY n DESC, a.id LIMIT :top"
        ).bindparams(uid=user_id, since=since, top=TOP)
    )
    artists = await session.scalar(
        text(
            "SELECT count(DISTINCT ta.artist_id) FROM play_history ph "
            "JOIN track_artists ta ON ta.track_id = ph.track_id "
            "WHERE ph.user_id = :uid AND ph.played_at >= :since"
        ).bindparams(uid=user_id, since=since)
    )
    sources = await session.execute(
        text(
            "SELECT source, count(*) AS n FROM play_history "
            "WHERE user_id = :uid AND played_at >= :since GROUP BY source ORDER BY n DESC"
        ).bindparams(uid=user_id, since=since)
    )
    return Stats(
        days=days,
        start=start,
        minutes=round(int(totals.secs) / 60),
        plays=int(totals.plays),
        songs=int(totals.songs),
        artists=int(artists or 0),
        previous_minutes=round(int(previous or 0) / 60),
        daily_minutes=[round(by_day.get(start + timedelta(days=i), 0) / 60) for i in range(days)],
        hourly_plays=hourly,
        top_track_ids=[(int(r.track_id), int(r.n)) for r in top_tracks],
        top_artists=[
            (int(r.id), str(r.latin_name if lang == "en" and r.latin_name else r.name), int(r.n))
            for r in top_artists
        ],
        sources={str(r.source): int(r.n) for r in sources},
    )
