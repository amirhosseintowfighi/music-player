"""Listening stats: local days, the period before, top songs and artists."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track
from app.services import listening_stats
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel


async def play(session: AsyncSession, uid: int, tid: int, at: datetime, secs: int = 180) -> None:
    await session.execute(
        text(
            "INSERT INTO play_history (user_id, track_id, played_at, duration_played, completed, "
            "source) VALUES (:u, :t, :at, :s, true, 'playlist')"
        ).bindparams(u=uid, t=tid, at=at, s=secs)
    )


async def test_stats_for_a_week(client: httpx.AsyncClient, session: AsyncSession) -> None:
    body = await login(client, 1030)
    auth, uid = bearer(body), body["me"]["id"]
    channel = await make_channel(session, "statschan")
    await ingest_items(
        session, channel, [item("Ebi - Khaneh", msg=1), item("Googoosh - Pol", msg=2)]
    )
    ebi, googoosh = (await session.scalars(select(Track.id).order_by(Track.id))).all()
    now = datetime.now(UTC)
    for _ in range(3):
        await play(session, uid, ebi, now - timedelta(minutes=5))
    await play(session, uid, googoosh, now - timedelta(days=2), secs=600)
    # Before the period: counted only as "the week before".
    await play(session, uid, googoosh, now - timedelta(days=9), secs=1200)
    await session.commit()

    stats = (await client.get("/v1/me/stats", headers=auth)).json()
    assert stats["days"] == 7
    assert stats["plays"] == 4
    assert stats["minutes"] == round((3 * 180 + 600) / 60)
    assert stats["previous_minutes"] == 20
    assert (stats["songs"], stats["artists"]) == (2, 2)
    assert len(stats["daily_minutes"]) == 7
    assert stats["daily_minutes"][-1] == 9
    assert sum(stats["daily_minutes"]) == stats["minutes"]
    assert sum(stats["hourly_plays"]) == 4
    assert [t["track"]["id"] for t in stats["top_tracks"]] == [ebi, googoosh]
    assert stats["top_tracks"][0]["plays"] == 3
    assert stats["top_artists"][0]["name"] == "ابی"  # the viewer reads Persian
    assert stats["sources"] == {"playlist": 4}

    month = (await client.get("/v1/me/stats?days=30", headers=auth)).json()
    assert len(month["daily_minutes"]) == 30
    assert month["plays"] == 5
    assert (await client.get("/v1/me/stats?days=12", headers=auth)).status_code == 422


async def test_a_quiet_listener_gets_zeros(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    body = await login(client, 1031)
    s = await listening_stats.for_user(session, body["me"]["id"], 7)
    assert (s.minutes, s.plays, s.previous_minutes) == (0, 0, 0)
    assert s.daily_minutes == [0] * 7
    assert s.hourly_plays == [0] * 24
