"""Social: public profiles, following, the friends feed and Wrapped.

Privacy is the whole design here, so it is enforced in one place rather than sprinkled
over the endpoints:

- ``users.public_profile`` is the single switch. Off means the profile is invisible to
  everyone else (404, not "private" — a 403 still confirms the account exists) and the
  person disappears from other people's feeds, while their own app keeps working.
- Listening activity is only ever shown for people you **follow**, never for strangers,
  and never from a private or banned account.
- Wrapped is computed from the user's own history and stored per year, so re-opening it
  in January does not recompute December.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, InvalidInput, NotFound
from app.models import Follow, User, WrappedReport
from tmusic_common.logging import get_logger

log = get_logger(__name__)

FEED_WINDOW_HOURS = 72
TOP_ITEMS = 10
MAX_FOLLOWING = 1000  # a following list, not a crawler


@dataclass(frozen=True, slots=True)
class Profile:
    user_id: int
    first_name: str
    username: str | None
    is_pro: bool
    followers: int
    following: int
    playlists: int
    tracks_played: int
    joined_at: datetime
    is_me: bool
    is_following: bool
    top_artists: list[dict[str, Any]]
    top_tracks: list[int]
    public_playlists: list[dict[str, Any]]


async def _visible_user(session: AsyncSession, user_id: int, viewer_id: int) -> User:
    """The one place that decides whether a profile may be seen at all."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    if user.id == viewer_id:
        return user
    if not user.public_profile or user.is_banned:
        # Deliberately the same answer as a missing user: a 403 would confirm it exists.
        raise NotFound("user not found")
    return user


async def profile(session: AsyncSession, user_id: int, viewer_id: int) -> Profile:
    user = await _visible_user(session, user_id, viewer_id)
    is_me = user.id == viewer_id

    counts = (
        await session.execute(
            text(
                """
        SELECT
          (SELECT count(*) FROM playlists WHERE user_id = :id AND kind = 'manual'
             AND (is_public OR :is_me))                                    AS playlists,
          (SELECT count(*) FROM play_history WHERE user_id = :id)          AS plays
        """
            ).bindparams(id=user.id, is_me=is_me)
        )
    ).one()

    following = False
    if not is_me:
        found = await session.scalar(
            select(func.count())
            .select_from(Follow)
            .where(Follow.follower_id == viewer_id, Follow.followee_id == user.id)
        )
        following = bool(found)

    artists = await session.execute(
        text(
            """
        SELECT a.id, a.name, count(*) AS plays
          FROM play_history h
          JOIN track_artists ta ON ta.track_id = h.track_id AND ta.role = 'primary'
          JOIN artists a ON a.id = ta.artist_id
         WHERE h.user_id = :id AND h.played_at > now() - interval '180 days'
         GROUP BY 1, 2 ORDER BY 3 DESC LIMIT :limit
        """
        ).bindparams(id=user.id, limit=TOP_ITEMS)
    )
    tracks = await session.execute(
        text(
            """
        SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id, count(*) AS plays
          FROM play_history h JOIN tracks t ON t.id = h.track_id
         WHERE h.user_id = :id AND h.played_at > now() - interval '180 days' AND NOT t.hidden
         GROUP BY 1 ORDER BY 2 DESC LIMIT :limit
        """
        ).bindparams(id=user.id, limit=TOP_ITEMS)
    )
    playlists = await session.execute(
        text(
            """
        SELECT id, name, tracks_count, share_slug FROM playlists
         WHERE user_id = :id AND kind = 'manual' AND (is_public OR :is_me)
         ORDER BY updated_at DESC LIMIT 20
        """
        ).bindparams(id=user.id, is_me=is_me)
    )

    return Profile(
        user_id=user.id,
        first_name=user.first_name,
        username=user.username,
        is_pro=user.plan_code != "free",
        followers=user.followers_count,
        following=user.following_count,
        playlists=int(counts.playlists),
        tracks_played=int(counts.plays),
        joined_at=user.created_at,
        is_me=is_me,
        is_following=following,
        top_artists=[{"id": row[0], "name": row[1], "plays": int(row[2])} for row in artists],
        top_tracks=[row[0] for row in tracks],
        public_playlists=[
            {"id": row[0], "name": row[1], "tracks_count": row[2], "share_slug": row[3]}
            for row in playlists
        ],
    )


# ── following ─────────────────────────────────────────────────────────────────


async def follow(session: AsyncSession, follower_id: int, followee_id: int) -> bool:
    """Returns True when this created a new follow. Following a private account fails."""
    if follower_id == followee_id:
        raise InvalidInput("you cannot follow yourself")
    target = await _visible_user(session, followee_id, follower_id)

    count = await session.scalar(
        select(func.count()).select_from(Follow).where(Follow.follower_id == follower_id)
    )
    if (count or 0) >= MAX_FOLLOWING:
        raise Conflict("following limit reached", limit=MAX_FOLLOWING)

    result = await session.execute(
        insert(Follow)
        .values(follower_id=follower_id, followee_id=followee_id)
        .on_conflict_do_nothing()
        .returning(Follow.follower_id)
    )
    if result.first() is None:
        return False  # already following; counters must not move
    await _bump_counters(session, follower_id, followee_id, +1)
    log.info("social.follow", follower=follower_id, followee=target.id)
    return True


async def unfollow(session: AsyncSession, follower_id: int, followee_id: int) -> bool:
    result = await session.execute(
        text(
            "DELETE FROM follows WHERE follower_id = :a AND followee_id = :b RETURNING 1"
        ).bindparams(a=follower_id, b=followee_id)
    )
    if result.first() is None:
        return False
    await _bump_counters(session, follower_id, followee_id, -1)
    return True


async def _bump_counters(
    session: AsyncSession, follower_id: int, followee_id: int, by: int
) -> None:
    """Denormalised counters: a profile must not COUNT(*) two tables on every open."""
    await session.execute(
        text(
            "UPDATE users SET following_count = greatest(following_count + :by, 0) WHERE id = :id"
        ).bindparams(by=by, id=follower_id)
    )
    await session.execute(
        text(
            "UPDATE users SET followers_count = greatest(followers_count + :by, 0) WHERE id = :id"
        ).bindparams(by=by, id=followee_id)
    )


async def following_ids(session: AsyncSession, user_id: int) -> list[int]:
    rows = await session.scalars(select(Follow.followee_id).where(Follow.follower_id == user_id))
    return list(rows.all())


async def connections(
    session: AsyncSession, user_id: int, viewer_id: int, *, direction: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Followers of, or people followed by, a user — only ever public accounts."""
    await _visible_user(session, user_id, viewer_id)
    if direction == "followers":
        join = "f.follower_id = u.id AND f.followee_id = :id"
    elif direction == "following":
        join = "f.followee_id = u.id AND f.follower_id = :id"
    else:
        raise InvalidInput("unknown direction", direction=direction)

    rows = await session.execute(
        text(
            f"""
        SELECT u.id, u.first_name, u.username, u.plan_code <> 'free' AS is_pro,
               EXISTS (SELECT 1 FROM follows me
                        WHERE me.follower_id = :viewer AND me.followee_id = u.id) AS i_follow
          FROM follows f JOIN users u ON {join}
         WHERE u.public_profile AND NOT u.is_banned
         ORDER BY f.created_at DESC LIMIT :limit
        """
        ).bindparams(id=user_id, viewer=viewer_id, limit=limit)
    )
    return [
        {
            "user_id": row[0],
            "first_name": row[1],
            "username": row[2],
            "is_pro": row[3],
            "is_following": row[4],
        }
        for row in rows
    ]


# ── friends feed ──────────────────────────────────────────────────────────────


async def friends_activity(
    session: AsyncSession, user_id: int, limit: int = 30
) -> list[dict[str, Any]]:
    """What the people you follow have been listening to, one row per person+track.

    Only completed plays count, so a skip never shows up as "listening to".
    """
    rows = await session.execute(
        text(
            """
        SELECT DISTINCT ON (h.user_id, track_id)
               h.user_id, u.first_name, u.username,
               coalesce(t.canonical_track_id, h.track_id) AS track_id,
               h.played_at
          FROM play_history h
          JOIN follows f ON f.followee_id = h.user_id AND f.follower_id = :id
          JOIN users u ON u.id = h.user_id
          JOIN tracks t ON t.id = h.track_id
         WHERE h.played_at > now() - make_interval(hours => :hours)
           AND h.completed
           AND u.public_profile AND NOT u.is_banned AND NOT t.hidden
         ORDER BY h.user_id, track_id, h.played_at DESC
        """
        ).bindparams(id=user_id, hours=FEED_WINDOW_HOURS)
    )
    activity = [
        {
            "user_id": row[0],
            "first_name": row[1],
            "username": row[2],
            "track_id": row[3],
            "played_at": row[4],
        }
        for row in rows
    ]
    activity.sort(key=lambda entry: entry["played_at"], reverse=True)
    return activity[:limit]


# ── Wrapped ───────────────────────────────────────────────────────────────────


async def wrapped(
    session: AsyncSession, user_id: int, year: int | None = None, *, rebuild: bool = False
) -> dict[str, Any]:
    """The user's year in music. Stored once per year, then read from the row."""
    target_year = year or datetime.now(UTC).year
    if not rebuild:
        stored = await session.get(WrappedReport, (user_id, target_year))
        if stored is not None:
            return dict(stored.payload)

    totals = (
        await session.execute(
            text(
                """
        SELECT count(*) AS plays,
               coalesce(sum(duration_played), 0) AS seconds,
               count(DISTINCT track_id) AS tracks,
               count(DISTINCT date_trunc('day', played_at)) AS days
          FROM play_history
         WHERE user_id = :id AND extract(year FROM played_at) = :year
        """
            ).bindparams(id=user_id, year=target_year)
        )
    ).one()

    artists = await session.execute(
        text(
            """
        SELECT a.id, a.name, count(*) AS plays
          FROM play_history h
          JOIN track_artists ta ON ta.track_id = h.track_id AND ta.role = 'primary'
          JOIN artists a ON a.id = ta.artist_id
         WHERE h.user_id = :id AND extract(year FROM h.played_at) = :year
         GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5
        """
        ).bindparams(id=user_id, year=target_year)
    )
    tracks = await session.execute(
        text(
            """
        SELECT coalesce(t.canonical_track_id, h.track_id) AS track_id, t.title, count(*) AS plays
          FROM play_history h JOIN tracks t ON t.id = h.track_id
         WHERE h.user_id = :id AND extract(year FROM h.played_at) = :year
         GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5
        """
        ).bindparams(id=user_id, year=target_year)
    )
    busiest = (
        await session.execute(
            text(
                """
        SELECT date_trunc('day', played_at)::date AS day, count(*) AS plays
          FROM play_history
         WHERE user_id = :id AND extract(year FROM played_at) = :year
         GROUP BY 1 ORDER BY 2 DESC LIMIT 1
        """
            ).bindparams(id=user_id, year=target_year)
        )
    ).one_or_none()

    payload: dict[str, Any] = {
        "year": target_year,
        "plays": int(totals.plays),
        "minutes": int(totals.seconds) // 60,
        "unique_tracks": int(totals.tracks),
        "active_days": int(totals.days),
        "top_artists": [{"id": row[0], "name": row[1], "plays": int(row[2])} for row in artists],
        "top_tracks": [{"id": row[0], "title": row[1], "plays": int(row[2])} for row in tracks],
        "busiest_day": (
            {"day": busiest[0].isoformat(), "plays": int(busiest[1])} if busiest else None
        ),
    }

    # A year with nothing in it is not worth storing: next time there may be data.
    if payload["plays"] > 0:
        await session.execute(
            insert(WrappedReport)
            .values(user_id=user_id, year=target_year, payload=payload)
            .on_conflict_do_update(
                index_elements=[WrappedReport.user_id, WrappedReport.year],
                set_={"payload": payload, "created_at": func.now()},
            )
        )
    return payload


async def set_public_profile(session: AsyncSession, user_id: int, public: bool) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    user.public_profile = public
    # Flushed because the feed and profile queries are raw SQL: without this they
    # would keep reading the old value for the rest of the transaction.
    await session.flush()
    return user
