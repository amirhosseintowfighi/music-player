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

    hidden = await client.put(f"/v1/tracks/{sangam}/hide", headers=headers)
    assert hidden.json() == {"track_id": sangam, "until": None}
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


# ── snooze, taste profile, pins (0016) ────────────────────────────────────────


async def test_snoozed_songs_come_back_by_themselves(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    me = await login(client, 5120)
    headers = bearer(me)
    pol = catalog["tracks"]["Pol"]
    snoozed = (
        await client.put(f"/v1/tracks/{pol}/hide", json={"snooze_days": 30}, headers=headers)
    ).json()
    assert snoozed["until"] is not None
    assert (await client.get("/v1/me/hidden", headers=headers)).json() == [pol]
    detail = (await client.get("/v1/me/hidden/tracks", headers=headers)).json()
    assert detail[0]["track_id"] == pol and detail[0]["until"]
    too_long = await client.put(
        f"/v1/tracks/{pol}/hide", json={"snooze_days": 365}, headers=headers
    )
    assert too_long.status_code == 422

    # Thirty days later it is simply back.
    await session.execute(
        text(
            "UPDATE hidden_tracks SET until = now() - interval '1 minute' WHERE user_id = :u"
        ).bindparams(u=me["me"]["id"])
    )
    await session.commit()
    assert (await client.get("/v1/me/hidden", headers=headers)).json() == []

    # Hiding again for good replaces the snooze.
    forever = (await client.put(f"/v1/tracks/{pol}/hide", headers=headers)).json()
    assert forever["until"] is None
    assert (await client.get("/v1/me/hidden", headers=headers)).json() == [pol]


async def test_a_playlist_can_stay_out_of_the_taste_profile(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    me = await login(client, 5121)
    headers = bearer(me)
    tracks = catalog["tracks"]
    sleep = (
        await client.post(
            "/v1/playlists",
            json={"name": "Sleep", "track_ids": [tracks["Talaagh"]]},
            headers=headers,
        )
    ).json()
    updated = await client.patch(
        f"/v1/playlists/{sleep['id']}", json={"exclude_from_taste": True}, headers=headers
    )
    assert updated.json()["exclude_from_taste"] is True

    plays: list[tuple[str, dict[str, Any]]] = [
        ("Talaagh", {"source": "playlist", "source_id": sleep["id"]}),
        ("Pol", {}),
    ]
    for title, source in plays:
        await client.post(
            "/v1/history",
            json={"track_id": tracks[title], "duration_played": 180, "completed": True, **source},
            headers=headers,
        )
    seeds = await recommendations._seed_tracks(session, me["me"]["id"])
    assert tracks["Pol"] in seeds
    assert tracks["Talaagh"] not in seeds

    # Nobody else can change it.
    other = bearer(await login(client, 5122))
    denied = await client.patch(
        f"/v1/playlists/{sleep['id']}", json={"exclude_from_taste": False}, headers=other
    )
    assert denied.status_code == 404


async def test_pins_sit_on_top_of_the_library(
    client: httpx.AsyncClient, catalog: dict[str, Any]
) -> None:
    headers = bearer(await login(client, 5123))
    stranger = bearer(await login(client, 5124))
    made = [
        (await client.post("/v1/playlists", json={"name": f"P{i}"}, headers=headers)).json()["id"]
        for i in range(2)
    ]
    ebi, googoosh = catalog["artists"]["ابی"], catalog["artists"]["گوگوش"]
    assert (await client.put(f"/v1/me/pins/artist/{ebi}", headers=headers)).status_code == 200
    for playlist_id in made:
        await client.put(f"/v1/me/pins/playlist/{playlist_id}", headers=headers)
    await client.put(f"/v1/me/pins/artist/{googoosh}", headers=headers)
    pins = (await client.get("/v1/me/pins", headers=headers)).json()
    assert pins[0] == {"kind": "artist", "ref_id": ebi}
    assert [p["ref_id"] for p in pins[1:3]] == made
    assert pins[3] == {"kind": "artist", "ref_id": googoosh}

    # Four at most; pinning one already pinned is fine.
    dariush = catalog["artists"]["داریوش"]
    assert (await client.put(f"/v1/me/pins/artist/{dariush}", headers=headers)).status_code == 409
    assert (await client.put(f"/v1/me/pins/artist/{ebi}", headers=headers)).status_code == 200
    # Someone else's private playlist cannot be pinned, and bad kinds are refused.
    assert (
        await client.put(f"/v1/me/pins/playlist/{made[0]}", headers=stranger)
    ).status_code == 404
    assert (await client.put("/v1/me/pins/album/1", headers=headers)).status_code == 422

    after = (await client.delete(f"/v1/me/pins/artist/{ebi}", headers=headers)).json()
    assert len(after) == 3
    # A deleted playlist drops off by itself.
    await client.delete(f"/v1/playlists/{made[0]}", headers=headers)
    left = (await client.get("/v1/me/pins", headers=headers)).json()
    assert [p["ref_id"] for p in left] == [made[1], googoosh]


# ── Fans also like, credits ───────────────────────────────────────────────────


async def test_fans_also_like(
    client: httpx.AsyncClient, session: AsyncSession, catalog: dict[str, Any]
) -> None:
    headers = bearer(await login(client, 5125))
    tracks, artists = catalog["tracks"], catalog["artists"]
    googoosh, ebi = artists["گوگوش"], artists["ابی"]
    # Nothing in common yet: no one listens, nothing is similar.
    assert (await client.get(f"/v1/artists/{googoosh}/related", headers=headers)).json() == []

    await session.execute(
        text(
            "INSERT INTO track_similarity (track_id, similar_id, score) VALUES (:a, :b, 0.8)"
        ).bindparams(a=tracks["Pol"], b=tracks["Khaneh"])
    )
    await session.commit()
    related = (await client.get(f"/v1/artists/{googoosh}/related", headers=headers)).json()
    assert [a["id"] for a in related] == [ebi]
    assert (await client.get("/v1/artists/999999/related", headers=headers)).status_code == 404


async def test_fans_also_like_from_co_listening(
    client: httpx.AsyncClient, catalog: dict[str, Any]
) -> None:
    tracks, artists = catalog["tracks"], catalog["artists"]
    headers: dict[str, str] = {}
    for telegram_id in (5126, 5127):
        headers = bearer(await login(client, telegram_id))
        for title in ("Pol", "Shabe Eshgh"):
            await client.post(
                "/v1/history",
                json={"track_id": tracks[title], "duration_played": 120, "completed": True},
                headers=headers,
            )
    related = (await client.get(f"/v1/artists/{artists['گوگوش']}/related", headers=headers)).json()
    assert artists["ابی"] in [a["id"] for a in related]


async def test_credits_name_the_people_and_the_channels(
    client: httpx.AsyncClient, catalog: dict[str, Any]
) -> None:
    headers = bearer(await login(client, 5128))
    pol = catalog["tracks"]["Pol"]
    credits = (await client.get(f"/v1/tracks/{pol}/credits", headers=headers)).json()
    assert credits["track"]["id"] == pol
    assert credits["track"]["artists"][0]["role"] == "primary"
    assert credits["channels"] >= 1
    assert credits["sources"][0]["username"] == "listenchan"
    assert credits["first_posted_at"]
    assert (await client.get("/v1/tracks/999999/credits", headers=headers)).status_code == 404


# ── Connect ───────────────────────────────────────────────────────────────────


async def test_connect_hands_the_music_to_another_device(
    client: httpx.AsyncClient, catalog: dict[str, Any]
) -> None:
    headers = bearer(await login(client, 5129))
    stranger = bearer(await login(client, 5130))
    pol, khaneh = catalog["tracks"]["Pol"], catalog["tracks"]["Khaneh"]

    phone = {"device_id": "phone-123", "name": "iPhone", "kind": "phone"}
    laptop = {"device_id": "laptop-456", "name": "Telegram Desktop", "kind": "desktop"}
    alone = (
        await client.post(
            "/v1/connect/heartbeat",
            json={**phone, "state": {"track_id": pol, "position_s": 72, "playing": True}},
            headers=headers,
        )
    ).json()
    assert alone == {"devices": [], "commands": []}

    seen = (await client.post("/v1/connect/heartbeat", json=laptop, headers=headers)).json()
    assert [d["id"] for d in seen["devices"]] == ["phone-123"]
    assert seen["devices"][0]["track"]["id"] == pol and seen["devices"][0]["playing"] is True
    # Another account sees none of it.
    theirs = await client.post("/v1/connect/heartbeat", json=laptop, headers=stranger)
    assert theirs.json()["devices"] == []

    moved = await client.post(
        "/v1/connect/command",
        json={
            "target": "laptop-456",
            "sender": "phone-123",
            "action": "transfer",
            "track_ids": [pol, khaneh],
            "index": 0,
            "position_s": 72,
        },
        headers=headers,
    )
    assert moved.status_code == 204
    inbox = (await client.post("/v1/connect/heartbeat", json=laptop, headers=headers)).json()
    command = inbox["commands"][0]
    assert command["action"] == "transfer" and command["position_s"] == 72
    assert [t["id"] for t in command["items"]] == [pol, khaneh]
    # Delivered once.
    again = (await client.post("/v1/connect/heartbeat", json=laptop, headers=headers)).json()
    assert again["commands"] == []

    nowhere = await client.post(
        "/v1/connect/command",
        json={"target": "gone-999", "sender": "phone-123", "action": "pause"},
        headers=headers,
    )
    assert nowhere.status_code == 404
    empty = await client.post(
        "/v1/connect/command",
        json={"target": "laptop-456", "sender": "phone-123", "action": "transfer"},
        headers=headers,
    )
    assert empty.status_code == 422

    forgot = await client.delete("/v1/connect/devices/phone-123", headers=headers)
    assert forgot.status_code == 204
    after = (await client.post("/v1/connect/heartbeat", json=laptop, headers=headers)).json()
    assert after["devices"] == []


async def test_stale_devices_drop_off(redis: Any) -> None:
    from app.services import connect

    device = connect.Device(id="old-device", name="Old", kind="web", seen=0)
    await connect.heartbeat(redis, 1, device, now=1000.0)
    assert [d.id for d in await connect.devices(redis, 1, now=1000.0 + 5)] == ["old-device"]
    assert await connect.devices(redis, 1, now=1000.0 + connect.STALE_S + 1) == []
