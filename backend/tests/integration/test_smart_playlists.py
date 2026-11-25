"""Smart playlists: rules in, songs out, kept current (0018)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Playlist, Track
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe


@pytest.fixture
async def lib(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    body = await login(client, 950)
    channel = await make_channel(session, "smartchan")
    other = await make_channel(session, "otherchan")
    await ingest_items(
        session,
        channel,
        [
            item("Ebi - Khaneh", msg=1, fuid="AgADs1", duration=180),
            item("Ebi - Shabe Eshgh", msg=2, fuid="AgADs2", duration=320),
            item("Googoosh - Pol", msg=3, fuid="AgADs3", duration=240),
            item("Moein - Barooni", msg=4, fuid="AgADs4", duration=200),
        ],
    )
    await ingest_items(session, other, [item("Dariush - Booye Eydi", msg=1, fuid="AgADs5")])
    await subscribe(session, body["me"]["id"], channel.id)
    await session.commit()
    ids = {
        fuid: tid
        for fuid, tid in (await session.execute(select(Track.file_unique_id, Track.id))).tuples()
    }
    ebi = await session.scalar(
        text(
            "SELECT ta.artist_id FROM track_artists ta WHERE ta.track_id = :t AND role = 'primary'"
        ).bindparams(t=ids["AgADs1"])
    )
    return {
        "auth": bearer(body),
        "user_id": body["me"]["id"],
        "ids": ids,
        "channel": channel.id,
        "other": other.id,
        "ebi": ebi,
    }


async def play(client: httpx.AsyncClient, auth: dict[str, str], track_id: int, times: int) -> None:
    for _ in range(times):
        resp = await client.post(
            "/v1/history",
            json={"track_id": track_id, "duration_played": 120, "completed": True},
            headers=auth,
        )
        assert resp.status_code == 204


async def test_rules_pick_songs_and_the_playlist_reads_like_any_other(
    client: httpx.AsyncClient, lib: dict[str, Any]
) -> None:
    ids = lib["ids"]
    created = await client.post(
        "/v1/playlists/smart",
        json={"name": "Ebi, short", "rules": {"artist_ids": [lib["ebi"]], "max_duration": 300}},
        headers=lib["auth"],
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "smart"
    assert body["rules"]["artist_ids"] == [lib["ebi"]]
    assert body["refreshed_at"] is not None
    assert [t["id"] for t in body["items"]] == [ids["AgADs1"]]
    assert body["tracks_count"] == 1

    # The playlist shows up in "my playlists" with its rules.
    mine = (await client.get("/v1/playlists", headers=lib["auth"])).json()
    assert [p["kind"] for p in mine] == ["smart"]
    assert mine[0]["rules"]["max_duration"] == 300

    # Changing the rules rebuilds the songs.
    changed = await client.put(
        f"/v1/playlists/{body['id']}/rules",
        json={"rules": {"artist_ids": [lib["ebi"]], "sort": "recent"}},
        headers=lib["auth"],
    )
    assert changed.status_code == 200
    assert [t["id"] for t in changed.json()["items"]] == [ids["AgADs2"], ids["AgADs1"]]


async def test_sources_and_listening_rules(client: httpx.AsyncClient, lib: dict[str, Any]) -> None:
    ids, auth = lib["ids"], lib["auth"]
    # The library is the default source: the unjoined channel's song is not in it.
    preview = await client.post("/v1/playlists/smart/preview", json={"rules": {}}, headers=auth)
    assert preview.json()["total"] == 4
    catalog = await client.post(
        "/v1/playlists/smart/preview",
        json={"rules": {"source": "catalog", "channel_ids": [lib["other"]]}},
        headers=auth,
    )
    assert [t["id"] for t in catalog.json()["items"]] == [ids["AgADs5"]]

    await client.put(f"/v1/tracks/{ids['AgADs3']}/like", headers=auth)
    likes = await client.post(
        "/v1/playlists/smart/preview", json={"rules": {"source": "likes"}}, headers=auth
    )
    assert [t["id"] for t in likes.json()["items"]] == [ids["AgADs3"]]

    await play(client, auth, ids["AgADs4"], 3)
    await play(client, auth, ids["AgADs2"], 1)
    most = await client.post(
        "/v1/playlists/smart/preview",
        json={"rules": {"min_my_plays": 1, "sort": "most_played"}},
        headers=auth,
    )
    assert [t["id"] for t in most.json()["items"]] == [ids["AgADs4"], ids["AgADs2"]]

    forgotten = await client.post(
        "/v1/playlists/smart/preview",
        json={"rules": {"not_played_days": 30, "sort": "recent"}},
        headers=auth,
    )
    assert [t["id"] for t in forgotten.json()["items"]] == [ids["AgADs3"], ids["AgADs1"]]


async def test_hidden_songs_stay_out(client: httpx.AsyncClient, lib: dict[str, Any]) -> None:
    ids, auth = lib["ids"], lib["auth"]
    assert (await client.put(f"/v1/tracks/{ids['AgADs1']}/hide", headers=auth)).status_code in (
        200,
        204,
    )
    preview = await client.post(
        "/v1/playlists/smart/preview", json={"rules": {"artist_ids": [lib["ebi"]]}}, headers=auth
    )
    assert [t["id"] for t in preview.json()["items"]] == [ids["AgADs2"]]


async def test_a_stale_smart_playlist_rebuilds_when_opened(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    ids, auth = lib["ids"], lib["auth"]
    created = (
        await client.post(
            "/v1/playlists/smart",
            json={"name": "Liked", "rules": {"source": "likes"}},
            headers=auth,
        )
    ).json()
    assert created["items"] == []
    await client.put(f"/v1/tracks/{ids['AgADs1']}/like", headers=auth)

    fresh = (await client.get(f"/v1/playlists/{created['id']}", headers=auth)).json()
    assert fresh["items"] == []  # not stale yet

    await session.execute(
        update(Playlist)
        .where(Playlist.id == created["id"])
        .values(refreshed_at=datetime.now(UTC) - timedelta(days=1))
    )
    await session.commit()
    rebuilt = (await client.get(f"/v1/playlists/{created['id']}", headers=auth)).json()
    assert [t["id"] for t in rebuilt["items"]] == [ids["AgADs1"]]

    # And on request, at any time.
    await client.put(f"/v1/tracks/{ids['AgADs2']}/like", headers=auth)
    refreshed = await client.post(f"/v1/playlists/{created['id']}/refresh", headers=auth)
    assert {t["id"] for t in refreshed.json()["items"]} == {ids["AgADs1"], ids["AgADs2"]}


async def test_smart_playlists_are_not_edited_by_hand(
    client: httpx.AsyncClient, lib: dict[str, Any]
) -> None:
    ids, auth = lib["ids"], lib["auth"]
    smart = (
        await client.post("/v1/playlists/smart", json={"name": "All", "rules": {}}, headers=auth)
    ).json()
    added = await client.post(
        f"/v1/playlists/{smart['id']}/tracks", json={"track_ids": [ids["AgADs5"]]}, headers=auth
    )
    assert added.status_code == 422
    assert added.json()["error"]["details"]["reason"] == "smart_playlist"
    removed = await client.delete(
        f"/v1/playlists/{smart['id']}/tracks/{ids['AgADs1']}", headers=auth
    )
    assert removed.status_code == 422

    manual = (await client.post("/v1/playlists", json={"name": "Mine"}, headers=auth)).json()
    not_smart = await client.put(
        f"/v1/playlists/{manual['id']}/rules", json={"rules": {}}, headers=auth
    )
    assert not_smart.status_code == 422
    assert not_smart.json()["error"]["details"]["reason"] == "not_smart"


async def test_contradictory_rules_are_refused(
    client: httpx.AsyncClient, lib: dict[str, Any]
) -> None:
    for rules in ({"min_duration": 300, "max_duration": 100}, {"year_from": 2020, "year_to": 2000}):
        resp = await client.post(
            "/v1/playlists/smart", json={"name": "x", "rules": rules}, headers=lib["auth"]
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["details"]["reason"] == "bad_rules"
    unknown = await client.post(
        "/v1/playlists/smart",
        json={"name": "x", "rules": {"sort": "alphabetical"}},
        headers=lib["auth"],
    )
    assert unknown.status_code == 422
