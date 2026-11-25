"""Listening streaks and badges (0021)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Claims, SessionDep, WritableClaims
from app.schemas import AchievementsOut, BadgeOut, StreakOut
from app.services import achievements

router = APIRouter(prefix="/v1/me", tags=["achievements"])


def _streak(streak: achievements.Streak) -> StreakOut:
    return StreakOut(
        current=streak.current,
        longest=streak.longest,
        today_done=streak.today_done,
        week=streak.week,
    )


@router.get("/streak", response_model=StreakOut)
async def my_streak(claims: Claims, session: SessionDep) -> StreakOut:
    """Just the streak: cheap enough for the home screen."""
    return _streak(await achievements.streak_for(session, claims.user_id))


@router.get("/achievements", response_model=AchievementsOut)
async def my_achievements(claims: Claims, session: SessionDep) -> AchievementsOut:
    """The streak and every badge, with progress. Newly reached badges are recorded."""
    streak = await achievements.streak_for(session, claims.user_id)
    badges = await achievements.badges_for(session, claims.user_id, streak.longest)
    out = [
        BadgeOut(
            code=b.code,
            goal=b.goal,
            progress=b.progress,
            earned=b.earned_at is not None,
            earned_at=b.earned_at,
            new=b.new,
        )
        for b in badges
    ]
    return AchievementsOut(
        streak=_streak(streak),
        badges=out,
        earned=sum(1 for b in out if b.earned),
        total=len(out),
    )


@router.post("/achievements/seen", status_code=204)
async def achievements_seen(claims: WritableClaims, session: SessionDep) -> None:
    """The listener has seen their new badges: stop calling them new."""
    await achievements.mark_seen(session, claims.user_id)
