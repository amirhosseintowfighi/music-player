"""Listening stats: the last week or month in numbers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Claims, SessionDep
from app.errors import InvalidInput
from app.schemas import ListeningStatsOut, StatsArtistOut, StatsTrackOut
from app.services import library, listening_stats

router = APIRouter(prefix="/v1/me", tags=["stats"])


@router.get("/stats", response_model=ListeningStatsOut)
async def my_stats(claims: Claims, session: SessionDep, days: int = 7) -> ListeningStatsOut:
    """Minutes per day and per hour, top songs and artists, and the period before.

    ``days`` is 7 or 30.
    """
    if days not in (7, 30):
        raise InvalidInput("days must be 7 or 30", reason="bad_period")
    s = await listening_stats.for_user(session, claims.user_id, days, lang=claims.lang)
    tracks = {
        t.id: t
        for t in await library.hydrate_tracks(
            session, [tid for tid, _ in s.top_track_ids], claims.lang, viewer_id=claims.user_id
        )
    }
    return ListeningStatsOut(
        days=s.days,
        start=s.start,
        minutes=s.minutes,
        plays=s.plays,
        songs=s.songs,
        artists=s.artists,
        previous_minutes=s.previous_minutes,
        daily_minutes=s.daily_minutes,
        hourly_plays=s.hourly_plays,
        top_tracks=[
            StatsTrackOut(track=tracks[tid], plays=n) for tid, n in s.top_track_ids if tid in tracks
        ],
        top_artists=[StatsArtistOut(id=a, name=name, plays=n) for a, name, n in s.top_artists],
        sources=s.sources,
    )
