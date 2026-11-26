"""Wake-up alarms (0020): local minutes, weekdays, ringing once, and the wake queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alarm, Notification
from app.services import alarms, notifications
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel

TEHRAN = 210


def test_next_fire_is_the_next_local_minute() -> None:
    # 2026-11-25 is a Wednesday. 05:00 UTC = 08:30 in Tehran.
    now = datetime(2026, 11, 25, 5, 0, tzinfo=UTC)
    # 07:00 Tehran has passed today: tomorrow, 03:30 UTC.
    assert alarms.next_fire(7 * 60, alarms.ALL_DAYS, TEHRAN, now) == datetime(
        2026, 11, 26, 3, 30, tzinfo=UTC
    )
    # 09:00 Tehran is still to come today.
    assert alarms.next_fire(9 * 60, alarms.ALL_DAYS, TEHRAN, now) == datetime(
        2026, 11, 25, 5, 30, tzinfo=UTC
    )
    # Weekdays only (Mon–Fri = 31), asked on a Friday evening: Monday.
    friday = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    monday = alarms.next_fire(7 * 60, 31, TEHRAN, friday)
    assert monday is not None and (monday + timedelta(minutes=TEHRAN)).weekday() == 0
    # Exactly on the minute is "already rung": the next one is a day later.
    on_time = datetime(2026, 11, 25, 3, 30, tzinfo=UTC)
    assert alarms.next_fire(7 * 60, 0, TEHRAN, on_time) == on_time + timedelta(days=1)


@pytest.fixture
async def sleeper(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    body = await login(client, 980)
    channel = await make_channel(session, "wakechan")
    await ingest_items(session, channel, [item("Ebi - Sobh", msg=1), item("Ebi - Aftab", msg=2)])
    await session.commit()
    ids = list((await session.scalars(text("SELECT id FROM tracks ORDER BY id"))).all())
    return {"auth": bearer(body), "user_id": body["me"]["id"], "ids": ids}


async def test_create_edit_and_list_alarms(
    client: httpx.AsyncClient, sleeper: dict[str, Any]
) -> None:
    auth = sleeper["auth"]
    created = await client.post(
        "/v1/alarms",
        json={"minute_of_day": 420, "days": 31, "tz_offset_minutes": TEHRAN, "label": "کار"},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    alarm = created.json()
    assert alarm["enabled"] is True
    assert alarm["next_fire_at"] is not None

    off = await client.patch(f"/v1/alarms/{alarm['id']}", json={"enabled": False}, headers=auth)
    assert off.json()["next_fire_at"] is None
    on = await client.patch(
        f"/v1/alarms/{alarm['id']}", json={"enabled": True, "minute_of_day": 450}, headers=auth
    )
    assert on.json()["minute_of_day"] == 450
    assert on.json()["next_fire_at"] is not None

    listed = (await client.get("/v1/alarms", headers=auth)).json()
    assert [a["id"] for a in listed] == [alarm["id"]]

    other = bearer(await login(client, 981))
    assert (await client.get(f"/v1/alarms/{alarm['id']}/wake", headers=other)).status_code == 404
    assert (await client.delete(f"/v1/alarms/{alarm['id']}", headers=auth)).status_code == 204
    assert (await client.get("/v1/alarms", headers=auth)).json() == []


async def test_a_playlist_alarm_needs_your_own_playlist(
    client: httpx.AsyncClient, sleeper: dict[str, Any]
) -> None:
    auth = sleeper["auth"]
    base = {"minute_of_day": 420, "tz_offset_minutes": TEHRAN, "source": "playlist"}
    missing = await client.post("/v1/alarms", json=base, headers=auth)
    assert missing.status_code == 422
    theirs = (
        await client.post(
            "/v1/playlists", json={"name": "x"}, headers=bearer(await login(client, 982))
        )
    ).json()
    stolen = await client.post(
        "/v1/alarms", json={**base, "playlist_id": theirs["id"]}, headers=auth
    )
    assert stolen.status_code == 404
    mine = (
        await client.post(
            "/v1/playlists",
            json={"name": "صبح", "track_ids": [sleeper["ids"][1]]},
            headers=auth,
        )
    ).json()
    ok = await client.post("/v1/alarms", json={**base, "playlist_id": mine["id"]}, headers=auth)
    assert ok.status_code == 201
    wake = (await client.get(f"/v1/alarms/{ok.json()['id']}/wake", headers=auth)).json()
    assert [t["id"] for t in wake["items"]] == [sleeper["ids"][1]]


async def test_no_more_than_five(client: httpx.AsyncClient, sleeper: dict[str, Any]) -> None:
    for minute in range(alarms.MAX_ALARMS):
        await client.post(
            "/v1/alarms",
            json={"minute_of_day": minute, "tz_offset_minutes": 0},
            headers=sleeper["auth"],
        )
    sixth = await client.post(
        "/v1/alarms", json={"minute_of_day": 99, "tz_offset_minutes": 0}, headers=sleeper["auth"]
    )
    assert sixth.status_code == 402


async def test_a_due_alarm_rings_through_quiet_hours_and_moves_on(
    client: httpx.AsyncClient, session: AsyncSession, sleeper: dict[str, Any]
) -> None:
    auth = sleeper["auth"]
    await client.put(f"/v1/tracks/{sleeper['ids'][0]}/like", headers=auth)
    daily = (
        await client.post(
            "/v1/alarms", json={"minute_of_day": 360, "tz_offset_minutes": TEHRAN}, headers=auth
        )
    ).json()
    once = (
        await client.post(
            "/v1/alarms",
            json={"minute_of_day": 365, "days": 0, "tz_offset_minutes": TEHRAN},
            headers=auth,
        )
    ).json()
    # 06:00 Tehran is inside quiet hours (23–09): an ordinary notice would wait.
    now = datetime(2026, 11, 25, 2, 40, tzinfo=UTC)  # 06:10 Tehran
    await session.execute(
        update(Alarm)
        .where(Alarm.id.in_([daily["id"], once["id"]]))
        .values(next_fire_at=now - timedelta(minutes=5))
    )
    await session.commit()

    assert await alarms.ring_due(session, now) == 2
    await session.commit()
    queued = (await session.scalars(select(Notification).order_by(Notification.id))).all()
    assert [n.kind for n in queued] == ["alarm", "alarm"]
    assert all(n.send_after <= datetime.now(UTC) for n in queued)
    assert queued[0].payload["open"] == f"wake/{daily['id']}"
    assert queued[0].payload["title"] == "Sobh"

    rows = {a.id: a for a in (await session.scalars(select(Alarm))).all()}
    for alarm in rows.values():
        await session.refresh(alarm)
    assert rows[daily["id"]].enabled is True
    assert rows[daily["id"]].next_fire_at is not None and rows[daily["id"]].next_fire_at > now
    assert rows[once["id"]].enabled is False
    assert rows[once["id"]].next_fire_at is None

    # Nothing is due any more: a second run in the same minute rings nobody.
    assert await alarms.ring_due(session, now) == 0


def test_the_wake_up_message() -> None:
    body = notifications.render("alarm", {"label": "", "title": "Sobh"}, "en")
    assert body == "⏰ Good morning!\nWake up to «Sobh»."
    plain = notifications.render("alarm", {"label": "Gym"}, "en")
    assert plain == "⏰ Time to wake up! Gym"


def test_the_button_opens_the_wake_screen(settings: Any) -> None:
    markup = notifications.keyboard(settings, "en", "wake/7")
    button = markup.inline_keyboard[0][0]
    assert button.web_app is not None
    assert button.web_app.url == f"{settings.webapp_url}#/wake/7"
    assert button.text == "▶️ Play my wake-up music"
