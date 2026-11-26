"""Listening streaks and badges (0021).

A **streak** is the run of consecutive days, in the listener's own time zone, with at
least one recorded play. It is still alive on a day nothing has been played *yet*:
"played yesterday" keeps it going until midnight. Plays in a private session are
not recorded, so they do not count — the same rule as everywhere else.

**Badges** are thresholds over a handful of aggregates. They are computed when asked
for and remembered the first time they are earned, so the date stays and the app
can celebrate a new one exactly once.

Everything comes from a few aggregate queries; nothing is added to the play path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserBadge

STREAK_DAYS = 400


@dataclass(frozen=True, slots=True)
class Badge:
    code: str
    metric: str
    goal: int


# Order is the order they are shown in: the easy ones first, then the long ones.
BADGES: tuple[Badge, ...] = (
    Badge("first_play", "plays", 1),
    Badge("streak_3", "longest_streak", 3),
    Badge("plays_100", "plays", 100),
    Badge("streak_7", "longest_streak", 7),
    Badge("collector", "likes", 50),
    Badge("curator", "playlists", 5),
    Badge("explorer", "artists", 50),
    Badge("night_owl", "night_plays", 30),
    Badge("early_bird", "morning_plays", 30),
    Badge("social", "following", 5),
    Badge("quiz_master", "quiz_best", 1500),
    Badge("streak_30", "longest_streak", 30),
    Badge("plays_1000", "plays", 1000),
    Badge("streak_100", "longest_streak", 100),
)


@dataclass(frozen=True, slots=True)
class Streak:
    current: int
    longest: int
    today_done: bool
    # The last seven local days, oldest first: did the listener play anything?
    week: list[bool]


def streaks(days: Iterable[date], today: date) -> Streak:
    """Current and longest runs of consecutive days, from the set of days with plays."""
    played = set(days)
    longest = run = 0
    previous: date | None = None
    for day in sorted(played):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    # Alive if it reaches today, or yesterday (today is not over yet).
    start = today if today in played else today - timedelta(days=1)
    current = 0
    while start - timedelta(days=current) in played:
        current += 1
    week = [today - timedelta(days=offset) in played for offset in range(6, -1, -1)]
    return Streak(current=current, longest=longest, today_done=today in played, week=week)


async def _offset(session: AsyncSession, user_id: int) -> int:
    offset = await session.scalar(select(User.tz_offset_minutes).where(User.id == user_id))
    return int(offset if offset is not None else 210)


async def streak_for(session: AsyncSession, user_id: int, now: datetime | None = None) -> Streak:
    now = now or datetime.now(UTC)
    tz = await _offset(session, user_id)
    rows = await session.execute(
        text(
            "SELECT DISTINCT (played_at + make_interval(mins => :tz))::date AS day "
            "FROM play_history WHERE user_id = :uid AND played_at > :since"
        ).bindparams(uid=user_id, tz=tz, since=now - timedelta(days=STREAK_DAYS))
    )
    today = (now + timedelta(minutes=tz)).date()
    return streaks((row.day for row in rows), today)


async def metrics(session: AsyncSession, user_id: int, longest_streak: int) -> dict[str, int]:
    tz = await _offset(session, user_id)
    plays = (
        await session.execute(
            text(
                "SELECT count(*) AS plays, "
                "count(*) FILTER (WHERE extract(hour FROM played_at + make_interval(mins => :tz)) "
                "BETWEEN 0 AND 3) AS night, "
                "count(*) FILTER (WHERE extract(hour FROM played_at + make_interval(mins => :tz)) "
                "BETWEEN 5 AND 7) AS morning "
                "FROM play_history WHERE user_id = :uid"
            ).bindparams(uid=user_id, tz=tz)
        )
    ).one()
    other = (
        await session.execute(
            text(
                "SELECT "
                "(SELECT count(DISTINCT ta.artist_id) FROM play_history ph "
                "  JOIN track_artists ta ON ta.track_id = ph.track_id "
                "  WHERE ph.user_id = :uid AND ph.played_at > now() - interval '365 days') "
                "  AS artists, "
                "(SELECT count(*) FROM likes WHERE user_id = :uid) AS likes, "
                "(SELECT count(*) FROM playlists WHERE user_id = :uid "
                "  AND kind IN ('manual', 'smart')) AS playlists, "
                "(SELECT count(*) FROM follows WHERE follower_id = :uid) AS following, "
                "(SELECT COALESCE(max(score), 0) FROM quiz_games "
                "  WHERE user_id = :uid AND finished_at IS NOT NULL) AS quiz_best"
            ).bindparams(uid=user_id)
        )
    ).one()
    return {
        "plays": int(plays.plays),
        "night_plays": int(plays.night),
        "morning_plays": int(plays.morning),
        "artists": int(other.artists),
        "likes": int(other.likes),
        "playlists": int(other.playlists),
        "following": int(other.following),
        "quiz_best": int(other.quiz_best),
        "longest_streak": longest_streak,
    }


@dataclass(frozen=True, slots=True)
class BadgeState:
    code: str
    goal: int
    progress: int
    earned_at: datetime | None
    new: bool


async def badges_for(session: AsyncSession, user_id: int, longest_streak: int) -> list[BadgeState]:
    """Every badge with its progress; newly earned ones are recorded on the way."""
    values = await metrics(session, user_id, longest_streak)
    earned_now = [b.code for b in BADGES if values[b.metric] >= b.goal]
    if earned_now:
        await session.execute(
            insert(UserBadge)
            .values([{"user_id": user_id, "badge": code} for code in earned_now])
            .on_conflict_do_nothing()
        )
    rows = {
        row.badge: row
        for row in (
            await session.scalars(select(UserBadge).where(UserBadge.user_id == user_id))
        ).all()
    }
    out = []
    for badge in BADGES:
        row = rows.get(badge.code)
        out.append(
            BadgeState(
                code=badge.code,
                goal=badge.goal,
                progress=min(values[badge.metric], badge.goal),
                earned_at=row.earned_at if row else None,
                new=bool(row and not row.seen),
            )
        )
    return out


async def mark_seen(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(UserBadge)
        .where(UserBadge.user_id == user_id, UserBadge.seen.is_(False))
        .values(seen=True)
    )
