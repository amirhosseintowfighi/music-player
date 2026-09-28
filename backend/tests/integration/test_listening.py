"""The listening features: This Is (Last.fm), follows and Release Radar, lyrics,
hiding a song, private sessions, long-track progress, folders, Blend, Daylist, DJ."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import AppState
from app.models import Artist, Track, TrackArtist
from app.services import blends, follows, lastfm, recommendations
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe


@pytest.fixture
async def catalog(session: AsyncSession) -> dict[str, Any]:
    channel = await make_channel(session, "listenchan")
    await ingest_items(
        session,
        channel,
        [
            item("Googoosh - Pol", msg=1, fuid="AgADl1", duration=200),
            item("Googoosh - Gole Sangam", msg=2, fuid="AgADl2", duration=210),
            item("Googoosh - Talaagh", msg=3, fuid="AgADl3", duration=220),
            item("Ebi - Khaneh", msg=4, fuid="AgADl4", duration=230),
            item("Ebi - Shabe Eshgh", msg=5, fuid="AgADl5", duration=240),
            item("Dariush - Podcast Episode 1", msg=6, fuid="AgADl6", duration=3600),
        ],
    )
    await session.commit()
    tracks = {
        title: track_id
        for title, track_id in (await session.execute(select(Track.title, Track.id))).tuples()
    }
    artists = {
        name: aid
        for name, aid in (
            await session.execute(
                select(Artist.name, Artist.id).join(TrackArtist, TrackArtist.artist_id == Artist.id)
            )
        ).tuples()
    }
    return {"channel": channel, "tracks": tracks, "artists": artists}


def mock_http(app_state: AppState, handler: Any) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response: httpx.Response = handler(request)
        return response

    app_state.http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return seen


# ── This Is ───────────────────────────────────────────────────────────────────


def test_title_keys_ignore_decoration() -> None:
    assert lastfm.title_key("Pol (Live)") == lastfm.title_key("pol")
    assert lastfm.title_key("Pol - Remastered 2011") == lastfm.title_key("Pol")
    assert lastfm.title_key("Pol feat. Ebi") == lastfm.title_key("Pol")


async def test_this_is_follows_lastfm_and_fills_from_our_plays(
    client: httpx.AsyncClient, app_state: AppState, catalog: dict[str, Any]
) -> None:
    app_state.settings = app_state.settings.model_copy(update={"lastfm_api_key": SecretStr("key")})
    seen = mock_http(
        app_state,
        lambda request: httpx.Response(
            200,
            json={
                "toptracks": {
                    "track": [
                        {"name": "Talaagh (Live)"},
                        {"name": "Something we do not have"},
                        {"name": "Pol"},
                    ]
                }
            },
        ),
    )
    headers = bearer(await login(client, 5101))
    googoosh = catalog["artists"]["گوگوش"]
    resp = await client.get(f"/v1/artists/{googoosh}/this-is", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "lastfm"
    titles = [t["title"] for t in body["items"]]
    # Last.fm's order for what we have, then the rest of the artist.
    assert titles[:2] == ["Talaagh", "Pol"]
    assert set(titles) == {"Talaagh", "Pol", "Gole Sangam"}
    assert seen[0].url.params["method"] == "artist.gettoptracks"

    # Asked once: the second read uses what was kept.
    await client.get(f"/v1/artists/{googoosh}/this-is", headers=headers)
    assert len(seen) == 1


async def test_this_is_without_lastfm_uses_our_order(
    client: httpx.AsyncClient, catalog: dict[str, Any]
) -> None:
    headers = bearer(await login(client, 5102))
    ebi = catalog["artists"]["ابی"]
    body = (await client.get(f"/v1/artists/{ebi}/this-is", headers=headers)).json()
    assert body["source"] == "plays"
    assert len(body["items"]) == 2
    assert (await client.get("/v1/artists/999999/this-is", headers=headers)).status_code == 404


async def test_lastfm_batch_marks_unknown_artists(
    session: AsyncSession, app_state: AppState, catalog: dict[str, Any]
) -> None:
    settings = app_state.settings.model_copy(update={"lastfm_api_key": SecretStr("key")})
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"error": 6, "message": "not found"})
        )
    )
    result = await lastfm.enrich_batch(session, http, settings, limit=50)
    assert result["looked_up"] >= 2 and result["found"] == 0
    unknown = await session.scalar(
        select(Artist.lastfm_top).where(Artist.id == catalog["artists"]["ابی"])
    )
    assert unknown == []
    # Without a key nothing is asked.
    assert await lastfm.enrich_batch(session, http, app_state.settings) == {
        "looked_up": 0,
        "found": 0,
    }


# ── follows, Release Radar, new-release notices ───────────────────────────────


async def test_follow_artist_feeds_release_radar_and_notices(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    me = await login(client, 5103)
    headers = bearer(me)
    ebi = catalog["artists"]["ابی"]
    resp = await client.put(f"/v1/artists/{ebi}/follow", headers=headers)
    assert resp.json() == {"following": True, "followers": 1}
    page = (await client.get(f"/v1/artists/{ebi}/page", headers=headers)).json()
    assert page["following"] and page["followers"] == 1
    assert [a["id"] for a in (await client.get("/v1/me/artists", headers=headers)).json()] == [ebi]

    radar = await recommendations.release_radar(session, me["me"]["id"])
    await session.commit()
    ids = await session.scalars(
        text("SELECT track_id FROM playlist_tracks WHERE playlist_id = :p").bindparams(p=radar.id)
    )
    assert set(ids.all()) == {catalog["tracks"]["Khaneh"], catalog["tracks"]["Shabe Eshgh"]}
    assert radar.kind == "release_radar"
    playlists = (await client.get("/v1/playlists", headers=headers)).json()
    assert any(p["kind"] == "release_radar" for p in playlists)
    discover = (await client.get("/v1/discover", headers=headers)).json()
    assert any(s["title"] == "رادار آهنگ‌های تازه" for s in discover["sections"])

    queued = await follows.queue_new_releases(session)
    assert queued == 1
    row = (
        await session.execute(
            text("SELECT kind, payload FROM notifications WHERE user_id = :u").bindparams(
                u=me["me"]["id"]
            )
        )
    ).one()
    assert row.kind == "new_release" and row.payload["count"] == 2
    # The same day does not notify twice.
    assert await follows.queue_new_releases(session) == 0
    await session.commit()

    resp = await client.delete(f"/v1/artists/{ebi}/follow", headers=headers)
    assert resp.json() == {"following": False, "followers": 0}
    assert (await client.put("/v1/artists/999999/follow", headers=headers)).status_code == 404


def test_new_release_copy() -> None:
    from app.services.notifications import render

    one = render("new_release", {"artist": "Ebi", "title": "Khaneh", "count": 1}, "en")
    assert one == "\U0001f195 New from Ebi: «Khaneh»"
    many = render("new_release", {"artist": "Ebi", "title": "Khaneh", "count": 3}, "fa")
    assert "Ebi" in many and "Khaneh" in many


# ── lyrics ────────────────────────────────────────────────────────────────────


async def test_lyrics_are_fetched_once_and_misses_remembered(
    client: httpx.AsyncClient, app_state: AppState, catalog: dict[str, Any]
) -> None:
    lrc = "[00:01.00] line one\n[00:05.50] line two"

    def lrclib(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/get":
            return httpx.Response(404, json={})
        if "Khaneh" in request.url.params["q"]:
            return httpx.Response(
                200,
                json=[
                    # A live version: wrong length, must not be taken.
                    {"duration": 300, "syncedLyrics": "[00:00.00] wrong"},
                    {"duration": 231, "syncedLyrics": lrc, "plainLyrics": "line one\nline two"},
                ],
            )
        return httpx.Response(200, json=[])

    seen = mock_http(app_state, lrclib)
    headers = bearer(await login(client, 5104))
    khaneh = catalog["tracks"]["Khaneh"]
    resp = await client.get(f"/v1/tracks/{khaneh}/lyrics", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["synced"] == lrc
    assert seen[0].headers["User-Agent"].startswith("tmusic")
    calls = len(seen)
    await client.get(f"/v1/tracks/{khaneh}/lyrics", headers=headers)
    assert len(seen) == calls

    pol = catalog["tracks"]["Pol"]
    resp = await client.get(f"/v1/tracks/{pol}/lyrics", headers=headers)
    assert resp.json() == {
        "track_id": pol,
        "found": False,
        "synced": None,
        "plain": None,
        "source": None,
    }
    calls = len(seen)
    await client.get(f"/v1/tracks/{pol}/lyrics", headers=headers)
    assert len(seen) == calls  # the miss was kept


async def test_lyrics_network_failure_is_not_kept(
    client: httpx.AsyncClient, app_state: AppState, catalog: dict[str, Any]
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    seen = mock_http(app_state, down)
    headers = bearer(await login(client, 5105))
    track = catalog["tracks"]["Talaagh"]
    for _ in range(2):
        resp = await client.get(f"/v1/tracks/{track}/lyrics", headers=headers)
        assert resp.json()["found"] is False
    assert len(seen) >= 2


# ── hide, private session, progress ───────────────────────────────────────────


async def test_hidden_tracks_leave_recommendations(
    client: httpx.AsyncClient, catalog: dict[str, Any]
) -> None:
    headers = bearer(await login(client, 5106))
    pol, sangam = catalog["tracks"]["Pol"], catalog["tracks"]["Gole Sangam"]
    recs = await client.post(
        "/v1/recommendations/for-tracks", json={"track_ids": [pol]}, headers=headers
    )
    assert sangam in [t["id"] for t in recs.json()["items"]]

    assert (await client.put(f"/v1/tracks/{sangam}/hide", headers=headers)).status_code == 204
    assert (await client.get("/v1/me/hidden", headers=headers)).json() == [sangam]
    recs = await client.post(
        "/v1/recommendations/for-tracks", json={"track_ids": [pol]}, headers=headers
    )
    assert sangam not in [t["id"] for t in recs.json()["items"]]
    assert (await client.delete(f"/v1/tracks/{sangam}/hide", headers=headers)).status_code == 204
    assert (await client.get("/v1/me/hidden", headers=headers)).json() == []


async def test_private_session_records_nothing(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    me = await login(client, 5107)
    headers = bearer(me)
    on = (await client.put("/v1/me/private-session", json={"on": True}, headers=headers)).json()
    assert on["private_until"] is not None
    play = {"track_id": catalog["tracks"]["Pol"], "duration_played": 120, "completed": True}
    assert (await client.post("/v1/history", json=play, headers=headers)).status_code == 204
    count = await session.scalar(
        text("SELECT count(*) FROM play_history WHERE user_id = :u").bindparams(u=me["me"]["id"])
    )
    assert count == 0
    off = (await client.put("/v1/me/private-session", json={"on": False}, headers=headers)).json()
    assert off["private_until"] is None
    assert (await client.get("/v1/me/private-session", headers=headers)).json() == {
        "private_until": None
    }
    await client.post("/v1/history", json=play, headers=headers)
    count = await session.scalar(
        text("SELECT count(*) FROM play_history WHERE user_id = :u").bindparams(u=me["me"]["id"])
    )
    assert count == 1


async def test_long_tracks_resume(client: httpx.AsyncClient, catalog: dict[str, Any]) -> None:
    headers = bearer(await login(client, 5108))
    episode = catalog["tracks"]["Podcast Episode 1"]
    saved = await client.put(
        f"/v1/tracks/{episode}/progress", json={"position_s": 900}, headers=headers
    )
    assert saved.json() == {"track_id": episode, "position_s": 900, "finished": False}
    got = (await client.get(f"/v1/me/progress?ids={episode}", headers=headers)).json()
    assert got == [{"track_id": episode, "position_s": 900, "finished": False}]
    listing = (await client.get("/v1/me/in-progress", headers=headers)).json()
    assert [t["id"] for t in listing["items"]] == [episode]

    done = await client.put(
        f"/v1/tracks/{episode}/progress", json={"position_s": 3580}, headers=headers
    )
    assert done.json()["finished"]
    assert (await client.get("/v1/me/in-progress", headers=headers)).json()["items"] == []


# ── folders and playlist recommendations ──────────────────────────────────────


async def test_folders_file_playlists(client: httpx.AsyncClient, catalog: dict[str, Any]) -> None:
    headers = bearer(await login(client, 5109))
    other = bearer(await login(client, 5110))
    playlist = (
        await client.post(
            "/v1/playlists",
            json={"name": "Road", "track_ids": [catalog["tracks"]["Pol"]]},
            headers=headers,
        )
    ).json()
    folder = (await client.post("/v1/folders", json={"name": "Trips"}, headers=headers)).json()
    assert folder["name"] == "Trips"

    resp = await client.put(
        f"/v1/playlists/{playlist['id']}/folder", json={"folder_id": folder["id"]}, headers=headers
    )
    assert resp.status_code == 204
    mine = (await client.get("/v1/playlists", headers=headers)).json()
    assert mine[0]["folder_id"] == folder["id"] and mine[0]["created_at"]
    assert (await client.get("/v1/folders", headers=headers)).json()[0]["playlists"] == 1

    # Another user can neither see nor use the folder.
    assert (await client.get("/v1/folders", headers=other)).json() == []
    resp = await client.put(
        f"/v1/playlists/{playlist['id']}/folder", json={"folder_id": folder["id"]}, headers=other
    )
    assert resp.status_code == 403

    renamed = await client.patch(
        f"/v1/folders/{folder['id']}", json={"name": "Road trips"}, headers=headers
    )
    assert renamed.json()["name"] == "Road trips"
    assert (await client.delete(f"/v1/folders/{folder['id']}", headers=headers)).status_code == 204
    # The playlist survives, unfiled.
    assert (await client.get("/v1/playlists", headers=headers)).json()[0]["folder_id"] is None

    recs = (
        await client.get(f"/v1/playlists/{playlist['id']}/recommendations", headers=headers)
    ).json()
    assert catalog["tracks"]["Pol"] not in [t["id"] for t in recs["items"]]
    assert catalog["tracks"]["Talaagh"] in [t["id"] for t in recs["items"]]


# ── Blend ─────────────────────────────────────────────────────────────────────


def test_blend_mix_and_match() -> None:
    assert blends.mix([1, 2, 3, 9], [9, 4, 5]) == [9, 1, 4, 2, 5, 3]
    assert blends.match_percent(set(), {1}) == 0
    assert blends.match_percent({1, 2}, {1, 2}) == 100
    assert 0 < blends.match_percent({1, 2, 3, 4}, {4, 5, 6, 7}) < 100


async def test_blend_between_two_listeners(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    sara = await login(client, 5111)
    ali = await login(client, 5112)
    tracks = catalog["tracks"]
    for token, liked in ((sara, ["Pol", "Khaneh"]), (ali, ["Khaneh", "Talaagh"])):
        for title in liked:
            await client.put(f"/v1/tracks/{tracks[title]}/like", headers=bearer(token))

    invite = (await client.post("/v1/blends/invite", headers=bearer(sara))).json()
    assert invite["share_url"].endswith(f"?startapp=bl_{invite['code']}")
    # Opening your own invite does nothing.
    own = await client.post(f"/v1/blends/join/{invite['code']}", headers=bearer(sara))
    assert own.status_code == 422

    joined = (await client.post(f"/v1/blends/join/{invite['code']}", headers=bearer(ali))).json()
    assert joined["other_name"] == "Test" and joined["match_pct"] > 0
    detail = (
        await client.get(f"/v1/playlists/{joined['playlist_id']}", headers=bearer(ali))
    ).json()
    ids = [t["id"] for t in detail["items"]]
    assert ids[0] == tracks["Khaneh"]  # what they both love comes first
    assert set(ids) == {tracks["Pol"], tracks["Khaneh"], tracks["Talaagh"]}
    assert detail["kind"] == "blend"

    listed = (await client.get("/v1/blends", headers=bearer(sara))).json()
    assert len(listed) == 1 and listed[0]["playlist_id"] != joined["playlist_id"]
    refreshed = await client.post(f"/v1/blends/{listed[0]['id']}/refresh", headers=bearer(sara))
    assert refreshed.status_code == 200
    assert await blends.refresh_all(session) == 1
    await session.commit()

    stranger = bearer(await login(client, 5113))
    assert (await client.delete(f"/v1/blends/{joined['id']}", headers=stranger)).status_code == 404
    assert (
        await client.delete(f"/v1/blends/{joined['id']}", headers=bearer(ali))
    ).status_code == 204
    assert (await client.get("/v1/blends", headers=bearer(sara))).json() == []
    assert (await client.post("/v1/blends/join/nope", headers=bearer(ali))).status_code == 404


# ── Daylist and DJ ────────────────────────────────────────────────────────────


def test_dayparts_follow_the_listeners_clock() -> None:
    from datetime import UTC, datetime

    noon_utc = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    assert recommendations.daypart(noon_utc, 0) == "afternoon"
    assert recommendations.daypart(noon_utc, 210) == "afternoon"  # 15:30 in Tehran
    assert recommendations.daypart(noon_utc, -420) == "morning"  # 05:00
    assert recommendations.daypart(datetime(2026, 9, 27, 23, 0, tzinfo=UTC), 0) == "night"
    assert recommendations.daypart(datetime(2026, 9, 27, 2, 0, tzinfo=UTC), 0) == "night"


async def test_daylist_and_dj(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    me = await login(client, 5114)
    headers = bearer(me)
    await subscribe(session, me["me"]["id"], catalog["channel"].id)
    await session.commit()
    for title in ("Pol", "Khaneh"):
        await client.post(
            "/v1/history",
            json={"track_id": catalog["tracks"][title], "duration_played": 180, "completed": True},
            headers=headers,
        )
    daylist = (await client.get("/v1/daylist", headers=headers)).json()
    assert daylist["part"] in {"morning", "afternoon", "evening", "night"}
    assert daylist["name"].startswith("دی‌لیست")
    assert daylist["items"]
    again = (await client.get("/v1/daylist", headers=headers)).json()
    assert again["playlist_id"] == daylist["playlist_id"]
    discover = (await client.get("/v1/discover", headers=headers)).json()
    assert not any(s.get("playlist_id") == daylist["playlist_id"] for s in discover["sections"])

    dj = (await client.get("/v1/dj", headers=headers)).json()
    kinds = [segment["kind"] for segment in dj["segments"]]
    assert kinds[0] == "favorites"
    seen = [t["id"] for segment in dj["segments"] for t in segment["items"]]
    assert len(seen) == len(set(seen))  # no song twice in a set
