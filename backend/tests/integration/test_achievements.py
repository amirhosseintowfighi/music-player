"""Streaks and badges (0021): local days, a streak that survives until midnight."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import achievements
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel

TODAY = date(2026, 11, 25)


def days(*offsets: int) -> list[date]:
    return [TODAY - timedelta(days=o) for o in offsets]


def test_a_streak_counts_back_from_today() -> None:
    s = achievements.streaks(days(0, 1, 2, 5, 6, 7, 8), TODAY)
    assert (s.current, s.longest, s.today_done) == (3, 4, True)
    assert s.week == [True, True, False, False, True, True, True]


def test_not_yet_today_keeps_yesterdays_streak_alive() -> None:
    s = achievements.streaks(days(1, 2), TODAY)
    assert (s.current, s.today_done) == (2, False)


def test_a_missed_day_ends_it() -> None:
    assert achievements.streaks(days(2, 3, 4), TODAY).current == 0
    assert achievements.streaks([], TODAY) == achievements.Streak(0, 0, False, [False] * 7)


async def test_achievements_api(client: httpx.AsyncClient, session: AsyncSession) -> None:
    body = await login(client, 990)
    auth, uid = bearer(body), body["me"]["id"]
    channel = await make_channel(session, "badgechan")
    await ingest_items(session, channel, [item("Ebi - Khaneh", msg=1)])
    track = await session.scalar(text("SELECT id FROM tracks LIMIT 1"))
    # Three days in a row in Tehran time (UTC+3:30), the last one being today.
    now = datetime.now(UTC)
    for back in (0, 1, 2):
        await session.execute(
            text(
                "INSERT INTO play_history (user_id, track_id, played_at, duration_played, "
                "completed, source) VALUES (:u, :t, :at, 200, true, 'library')"
            ).bindparams(u=uid, t=track, at=now - timedelta(days=back))
        )
    await session.commit()

    streak = (await client.get("/v1/me/streak", headers=auth)).json()
    assert streak["current"] == 3
    assert streak["today_done"] is True

    result = (await client.get("/v1/me/achievements", headers=auth)).json()
    by_code = {b["code"]: b for b in result["badges"]}
    assert by_code["first_play"]["earned"] is True
    assert by_code["first_play"]["new"] is True
    assert by_code["streak_3"]["earned"] is True
    assert by_code["streak_7"]["earned"] is False
    assert by_code["streak_7"]["progress"] == 3
    assert by_code["plays_100"]["progress"] == 3
    assert result["earned"] == 2
    assert result["total"] == len(achievements.BADGES)

    # Seen once, no longer new; the date it was earned stays.
    first = by_code["first_play"]["earned_at"]
    assert (await client.post("/v1/me/achievements/seen", headers=auth)).status_code == 204
    again = {
        b["code"]: b
        for b in (await client.get("/v1/me/achievements", headers=auth)).json()["badges"]
    }
    assert again["first_play"]["new"] is False
    assert again["first_play"]["earned_at"] == first


async def test_a_new_listener_has_nothing_yet(client: httpx.AsyncClient) -> None:
    auth = bearer(await login(client, 991))
    result = (await client.get("/v1/me/achievements", headers=auth)).json()
    assert result["earned"] == 0
    assert result["streak"]["current"] == 0
