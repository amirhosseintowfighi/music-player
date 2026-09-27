"""Jam: a shared queue and playhead, through the API and the bot."""

from __future__ import annotations

from typing import Any

import fakeredis
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track
from app.services import jams
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel
from tests.integration.test_bot import USER, RecordingSession, private_message, send
from tests.integration.test_bot import tg as tg


def command(text: str) -> dict[str, Any]:
    length = len(text.split(" ", 1)[0])
    return private_message(
        text=text, entities=[{"type": "bot_command", "offset": 0, "length": length}]
    )


@pytest.fixture
async def tracks(session: AsyncSession) -> list[int]:
    channel = await make_channel(session, "jamchan")
    await ingest_items(
        session,
        channel,
        [
            item("Moein - Shabe Barooni", msg=1, fuid="AgADj1", duration=180),
            item("Googoosh - Pol", msg=2, fuid="AgADj2", duration=200),
            item("Ebi - Khaneh", msg=3, fuid="AgADj3", duration=240),
        ],
    )
    await session.commit()
    rows = await session.execute(select(Track.id).order_by(Track.id))
    return list(rows.scalars())


def test_settle_moves_past_finished_tracks() -> None:
    jam = jams.Jam(
        code="abcdef",
        host_id=1,
        created_ms=0,
        items=[jams.Item(1, 100, 1), jams.Item(2, 50, 1), jams.Item(3, 60, 1)],
        playing=True,
        pos=90.0,
        at=0,
    )
    # 90s into a 100s track, 70s later: the second track ended too, 10s into the third.
    assert jams.settle(jam, 70_000)
    assert jam.index == 2
    assert jam.position(70_000) == pytest.approx(10.0)
    assert jam.rev == 1
    # Past the end of the queue it stops at the end of the last track.
    assert jams.settle(jam, 200_000)
    assert (jam.index, jam.playing, jam.pos) == (2, False, 60.0)
    assert not jams.settle(jam, 300_000)


def test_settle_waits_for_unknown_durations() -> None:
    jam = jams.Jam(
        code="abcdef", host_id=1, created_ms=0, items=[jams.Item(1, 0, 1)], playing=True, at=0
    )
    assert not jams.settle(jam, 10_000_000)
    assert jam.index == 0


def test_codes_are_normalized() -> None:
    assert jams.normalize_code(" ABCDEF ") == "abcdef"
    for bad in ("abc", "abcde0", "abcdefg"):
        with pytest.raises(jams.NotFound):
            jams.normalize_code(bad)


async def test_jam_lifecycle(client: httpx.AsyncClient, tracks: list[int]) -> None:
    host = bearer(await login(client, 7001))
    guest = bearer(await login(client, 7002))
    stranger = bearer(await login(client, 7003))

    resp = await client.post(
        "/v1/jams",
        json={"track_ids": tracks[:2], "index": 1, "position_s": 12, "playing": True},
        headers=host,
    )
    assert resp.status_code == 201, resp.text
    jam = resp.json()
    code = jam["code"]
    assert jam["is_host"] and jam["can_control"]
    assert jam["share_url"].endswith(f"?startapp=jam_{code}")
    assert [i["track"]["id"] for i in jam["items"]] == tracks[:2]
    assert jam["index"] == 1 and jam["playing"]
    assert jam["position_s"] >= 12

    assert (await client.get("/v1/jams/current", headers=guest)).json() is None
    # Controls need membership.
    resp = await client.post(f"/v1/jams/{code}/control", json={"action": "pause"}, headers=guest)
    assert resp.status_code == 403

    joined = (await client.post(f"/v1/jams/{code}/join", headers=guest)).json()
    assert [m["is_host"] for m in joined["members"]] == [True, False]
    assert not joined["is_host"] and joined["can_control"]
    assert (await client.get("/v1/jams/current", headers=guest)).json()["code"] == code

    # Polling with the queue revision already known skips the queue.
    polled = (await client.get(f"/v1/jams/{code}?qrev={jam['qrev']}", headers=guest)).json()
    assert polled["items"] is None and polled["queue_length"] == 2

    paused = (
        await client.post(f"/v1/jams/{code}/control", json={"action": "pause"}, headers=guest)
    ).json()
    assert not paused["playing"]
    sought = (
        await client.post(
            f"/v1/jams/{code}/control", json={"action": "seek", "position_s": 30}, headers=host
        )
    ).json()
    assert sought["position_s"] == 30

    # A guest adds music; "next" puts it right after the playing track.
    added = (
        await client.post(
            f"/v1/jams/{code}/queue",
            json={"track_ids": [tracks[2]], "position": "next"},
            headers=guest,
        )
    ).json()
    assert [i["track"]["id"] for i in added["items"]] == [tracks[0], tracks[1], tracks[2]]
    assert added["items"][2]["added_by"] == joined["members"][1]["user_id"]

    # Two listeners report the end of the same track: only one skip happens.
    for headers in (host, guest):
        resp = await client.post(
            f"/v1/jams/{code}/control",
            json={"action": "next", "expected_index": 1},
            headers=headers,
        )
        assert resp.status_code == 200
    assert resp.json()["index"] == 2 and resp.json()["playing"]

    back = (
        await client.post(f"/v1/jams/{code}/control", json={"action": "previous"}, headers=host)
    ).json()
    assert back["index"] == 1
    jumped = (
        await client.post(
            f"/v1/jams/{code}/control", json={"action": "jump", "index": 0}, headers=host
        )
    ).json()
    assert jumped["index"] == 0
    bad = await client.post(
        f"/v1/jams/{code}/control", json={"action": "jump", "index": 9}, headers=host
    )
    assert bad.status_code == 422

    # The host takes control back.
    locked = (
        await client.patch(f"/v1/jams/{code}", json={"guests_can_control": False}, headers=host)
    ).json()
    assert not locked["guests_can_control"]
    resp = await client.post(f"/v1/jams/{code}/control", json={"action": "next"}, headers=guest)
    assert resp.status_code == 403
    resp = await client.patch(f"/v1/jams/{code}", json={"guests_can_control": True}, headers=guest)
    assert resp.status_code == 403

    # A guest may remove what they added, but not the host's tracks.
    assert (await client.delete(f"/v1/jams/{code}/queue/1", headers=guest)).status_code == 403
    removed = (await client.delete(f"/v1/jams/{code}/queue/2", headers=guest)).json()
    assert removed["queue_length"] == 2
    assert (await client.delete(f"/v1/jams/{code}/queue/0", headers=host)).status_code == 422

    # Leaving keeps the jam; the host ending it removes it for everybody.
    assert (await client.post(f"/v1/jams/{code}/leave", headers=guest)).status_code == 204
    assert len((await client.get(f"/v1/jams/{code}", headers=host)).json()["members"]) == 1
    assert (await client.delete(f"/v1/jams/{code}", headers=stranger)).status_code == 403
    assert (await client.delete(f"/v1/jams/{code}", headers=host)).status_code == 204
    assert (await client.get(f"/v1/jams/{code}", headers=host)).status_code == 404
    assert (await client.get("/v1/jams/current", headers=host)).json() is None


async def test_empty_jam_starts_with_the_first_track_added(
    client: httpx.AsyncClient, tracks: list[int]
) -> None:
    host = bearer(await login(client, 7011))
    jam = (await client.post("/v1/jams", json={}, headers=host)).json()
    assert jam["queue_length"] == 0 and not jam["playing"]
    resp = await client.post(
        f"/v1/jams/{jam['code']}/control", json={"action": "play"}, headers=host
    )
    assert not resp.json()["playing"]
    added = (
        await client.post(
            f"/v1/jams/{jam['code']}/queue", json={"track_ids": [tracks[0], 999_999]}, headers=host
        )
    ).json()
    assert added["playing"] and added["index"] == 0 and added["queue_length"] == 1
    now = (
        await client.post(
            f"/v1/jams/{jam['code']}/queue",
            json={"track_ids": [tracks[1]], "position": "now"},
            headers=host,
        )
    ).json()
    assert now["index"] == 1 and now["position_s"] < 1
    missing = await client.post(
        f"/v1/jams/{jam['code']}/queue", json={"track_ids": [999_999]}, headers=host
    )
    assert missing.status_code == 422


async def test_starting_or_joining_another_jam_leaves_the_old_one(
    client: httpx.AsyncClient, tracks: list[int]
) -> None:
    a = bearer(await login(client, 7021))
    b = bearer(await login(client, 7022))
    first = (await client.post("/v1/jams", json={"track_ids": tracks}, headers=a)).json()
    second = (await client.post("/v1/jams", json={}, headers=b)).json()
    await client.post(f"/v1/jams/{first['code']}/join", headers=b)
    # b hosted `second`, so joining `first` ended it.
    assert (await client.get(f"/v1/jams/{second['code']}", headers=b)).status_code == 404
    # a starting a new jam ends `first`.
    await client.post("/v1/jams", json={}, headers=a)
    assert (await client.get(f"/v1/jams/{first['code']}", headers=a)).status_code == 404
    assert (await client.get("/v1/jams/nope00", headers=a)).status_code == 404


async def test_a_full_jam_turns_people_away(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jams, "MAX_MEMBERS", 1)
    host = bearer(await login(client, 7031))
    guest = bearer(await login(client, 7032))
    jam = (await client.post("/v1/jams", json={}, headers=host)).json()
    resp = await client.post(f"/v1/jams/{jam['code']}/join", headers=guest)
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["reason"] == "jam_full"


async def test_bot_jam_command(
    client: httpx.AsyncClient,
    tg: RecordingSession,
    redis: fakeredis.aioredis.FakeRedis,
) -> None:
    await send(client, command("/jam"))
    card = tg.calls[-1]
    assert "Sara" in card.text  # type: ignore[attr-defined]
    [key] = await redis.keys("jam:user:*")
    code = await redis.get(key)
    assert code is not None
    buttons = card.reply_markup.inline_keyboard  # type: ignore[attr-defined]
    assert buttons[0][0].url.endswith(f"?startapp=jam_{code}")
    assert buttons[1][0].url.startswith("https://t.me/share/url?url=")
    assert buttons[2][0].callback_data == f"jam:end:{code}"

    # Asking again shows the same jam rather than starting another.
    await send(client, command("/jam"))
    assert await redis.keys("jam:user:*") == [key]
    assert await redis.get(key) == code

    # A friend joins with the code.
    friend = bearer(await login(client, 7041))
    joined = await client.post(f"/v1/jams/{code}/join", headers=friend)
    assert joined.status_code == 200
    assert len(joined.json()["members"]) == 2

    await send(
        client,
        {
            "update_id": 99_001,
            "callback_query": {
                "id": "cbj",
                "from": USER,
                "chat_instance": "1",
                "data": f"jam:end:{code}",
            },
        },
    )
    assert await redis.get(f"jam:{code}") is None


async def test_bot_joins_by_code_and_deep_link(
    client: httpx.AsyncClient,
    tg: RecordingSession,
    redis: fakeredis.aioredis.FakeRedis,
) -> None:
    host = bearer(await login(client, 7051))
    code = (await client.post("/v1/jams", json={}, headers=host)).json()["code"]

    await send(client, command("/jam zzzzzz"))
    assert tg.texts()[-1] == jams_text("jam_not_found")

    await send(client, command(f"/start jam_{code}"))
    assert tg.texts()[-2].startswith("✅")
    card = tg.calls[-1]
    assert card.reply_markup.inline_keyboard[2][0].callback_data == f"jam:leave:{code}"  # type: ignore[attr-defined]
    members = (await client.get(f"/v1/jams/{code}", headers=host)).json()["members"]
    assert len(members) == 2

    await send(
        client,
        {
            "update_id": 99_002,
            "callback_query": {
                "id": "cbl",
                "from": USER,
                "chat_instance": "1",
                "data": f"jam:leave:{code}",
            },
        },
    )
    members = (await client.get(f"/v1/jams/{code}", headers=host)).json()["members"]
    assert len(members) == 1
    assert len(await redis.keys("jam:user:*")) == 1  # only the host is left


def jams_text(key: str) -> str:
    from app.bot.texts import t

    return t(key, "fa")
