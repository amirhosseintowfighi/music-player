"""Phase 5: playlists (ordering, sharing, collaboration), likes, history, resume."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlaylistTrack, Track, User
from app.services import plans
from app.services.ingest import ingest_items
from tests.conftest import bearer, init_data_for, login
from tests.integration.helpers import item, make_channel, subscribe


@pytest.fixture
async def lib(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    body = await login(client, 900)
    channel = await make_channel(session, "plchan")
    await ingest_items(
        session,
        channel,
        [
            item("Moein - Shabe Barooni", msg=1, fuid="AgADp1"),
            item("Googoosh - Pol", msg=2, fuid="AgADp2"),
            item("Ebi - Khaneh", msg=3, fuid="AgADp3"),
            item("Googoosh - Pol 🎵", msg=4, fuid="AgADp4"),  # duplicate of AgADp2
        ],
    )
    await subscribe(session, body["me"]["id"], channel.id)
    await session.commit()
    ids = {
        fuid: tid
        for fuid, tid in (await session.execute(select(Track.file_unique_id, Track.id))).tuples()
    }
    return {"auth": bearer(body), "user_id": body["me"]["id"], "ids": ids, "channel": channel.id}


async def make_pro(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(plan_code="pro_monthly", premium_until=datetime.now(UTC) + timedelta(days=30))
    )
    await session.commit()
    plans.clear_caches()


async def test_playlist_crud_and_ordering(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    ids = lib["ids"]
    created = await client.post(
        "/v1/playlists",
        json={"name": "  شب‌های بارونی  ", "track_ids": [ids["AgADp1"], ids["AgADp2"]]},
        headers=lib["auth"],
    )
    assert created.status_code == 201
    playlist_id = created.json()["id"]
    assert created.json()["name"] == "شب‌های بارونی"

    detail = (await client.get(f"/v1/playlists/{playlist_id}", headers=lib["auth"])).json()
    assert [t["id"] for t in detail["items"]] == [ids["AgADp1"], ids["AgADp2"]]
    assert detail["tracks_count"] == 2
    assert detail["duration_total"] == 400
    assert detail["can_edit"] is True

    # Adding a duplicate of a track already in the list is a no-op (canonical id).
    await client.post(
        f"/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [ids["AgADp4"], ids["AgADp3"]]},
        headers=lib["auth"],
    )
    detail = (await client.get(f"/v1/playlists/{playlist_id}", headers=lib["auth"])).json()
    assert [t["id"] for t in detail["items"]] == [ids["AgADp1"], ids["AgADp2"], ids["AgADp3"]]

    # Move the last track to the top, then between the first two.
    moved = await client.post(
        f"/v1/playlists/{playlist_id}/move",
        json={"track_id": ids["AgADp3"], "after_track_id": None},
        headers=lib["auth"],
    )
    assert moved.status_code == 204
    order = [
        t["id"]
        for t in (await client.get(f"/v1/playlists/{playlist_id}", headers=lib["auth"])).json()[
            "items"
        ]
    ]
    assert order == [ids["AgADp3"], ids["AgADp1"], ids["AgADp2"]]

    await client.post(
        f"/v1/playlists/{playlist_id}/move",
        json={"track_id": ids["AgADp2"], "after_track_id": ids["AgADp3"]},
        headers=lib["auth"],
    )
    order = [
        t["id"]
        for t in (await client.get(f"/v1/playlists/{playlist_id}", headers=lib["auth"])).json()[
            "items"
        ]
    ]
    assert order == [ids["AgADp3"], ids["AgADp2"], ids["AgADp1"]]

    removed = await client.delete(
        f"/v1/playlists/{playlist_id}/tracks/{ids['AgADp2']}", headers=lib["auth"]
    )
    assert removed.status_code == 204
    assert (
        await client.delete(
            f"/v1/playlists/{playlist_id}/tracks/{ids['AgADp2']}", headers=lib["auth"]
        )
    ).status_code == 404

    renamed = await client.patch(
        f"/v1/playlists/{playlist_id}", json={"name": "بارونی"}, headers=lib["auth"]
    )
    assert renamed.json()["name"] == "بارونی"
    assert renamed.json()["tracks_count"] == 2

    assert (
        await client.delete(f"/v1/playlists/{playlist_id}", headers=lib["auth"])
    ).status_code == 204
    assert (
        await client.get(f"/v1/playlists/{playlist_id}", headers=lib["auth"])
    ).status_code == 404


async def test_move_rebalances_when_positions_collapse(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    ids = lib["ids"]
    created = await client.post(
        "/v1/playlists",
        json={"name": "order", "track_ids": [ids["AgADp1"], ids["AgADp2"], ids["AgADp3"]]},
        headers=lib["auth"],
    )
    playlist_id = created.json()["id"]
    # Squeeze two neighbours together so the next split has no room.
    await session.execute(
        text(
            "UPDATE playlist_tracks SET position = 1000.0000001 "
            "WHERE playlist_id = :p AND track_id = :t"
        ).bindparams(p=playlist_id, t=ids["AgADp2"])
    )
    await session.execute(
        text(
            "UPDATE playlist_tracks SET position = 1000 WHERE playlist_id = :p AND track_id = :t"
        ).bindparams(p=playlist_id, t=ids["AgADp1"])
    )
    await session.commit()

    await client.post(
        f"/v1/playlists/{playlist_id}/move",
        json={"track_id": ids["AgADp3"], "after_track_id": ids["AgADp1"]},
        headers=lib["auth"],
    )
    order = [
        t["id"]
        for t in (await client.get(f"/v1/playlists/{playlist_id}", headers=lib["auth"])).json()[
            "items"
        ]
    ]
    assert order == [ids["AgADp1"], ids["AgADp3"], ids["AgADp2"]]
    positions = (
        await session.scalars(
            select(PlaylistTrack.position)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position)
        )
    ).all()
    gaps = [float(b) - float(a) for a, b in pairwise(positions)]
    assert all(gap >= 1 for gap in gaps)  # the list was re-spread


async def test_free_plan_playlist_limit(client: httpx.AsyncClient, lib: dict[str, Any]) -> None:
    for i in range(2):
        assert (
            await client.post("/v1/playlists", json={"name": f"p{i}"}, headers=lib["auth"])
        ).status_code == 201
    over = await client.post("/v1/playlists", json={"name": "p6"}, headers=lib["auth"])
    assert over.status_code == 402
    assert over.json()["error"]["details"] == {"kind": "playlists", "limit": 2}


async def test_sharing_requires_pro_and_produces_a_link(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    playlist_id = (
        await client.post(
            "/v1/playlists",
            json={"name": "mix", "track_ids": [lib["ids"]["AgADp1"]]},
            headers=lib["auth"],
        )
    ).json()["id"]

    denied = await client.patch(
        f"/v1/playlists/{playlist_id}", json={"is_public": True}, headers=lib["auth"]
    )
    assert denied.status_code == 402
    assert denied.json()["error"]["details"]["kind"] == "share_playlist"

    await make_pro(session, lib["user_id"])
    pro_auth = bearer(
        (await client.post("/v1/auth/telegram", json={"init_data": init_data_for(900)})).json()
    )
    shared = await client.patch(
        f"/v1/playlists/{playlist_id}", json={"is_public": True}, headers=pro_auth
    )
    assert shared.status_code == 200
    slug = shared.json()["share_slug"]
    assert slug and len(slug) == 10
    assert shared.json()["share_url"] == f"https://t.me/tmusic_test_bot?startapp=pl_{slug}"

    # Another user can open the link but cannot edit it.
    other = bearer(await login(client, 901))
    opened = await client.get(f"/v1/playlists/shared/{slug}", headers=other)
    assert opened.status_code == 200
    assert opened.json()["can_edit"] is False
    assert opened.json()["is_owner"] is False
    assert [t["title"] for t in opened.json()["items"]] == ["Shabe Barooni"]
    assert (
        await client.post(
            f"/v1/playlists/{playlist_id}/tracks",
            json={"track_ids": [lib["ids"]["AgADp2"]]},
            headers=other,
        )
    ).status_code == 403

    # A private playlist is invisible to others, and unknown slugs 404.
    private_id = (
        await client.post("/v1/playlists", json={"name": "secret"}, headers=pro_auth)
    ).json()["id"]
    assert (await client.get(f"/v1/playlists/{private_id}", headers=other)).status_code == 404
    assert (await client.get("/v1/playlists/shared/nosuchslug", headers=other)).status_code == 404


async def test_collaborative_playlist(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    await make_pro(session, lib["user_id"])
    pro_auth = bearer(
        (await client.post("/v1/auth/telegram", json={"init_data": init_data_for(900)})).json()
    )
    playlist_id = (
        await client.post("/v1/playlists", json={"name": "با هم"}, headers=pro_auth)
    ).json()["id"]
    updated = await client.patch(
        f"/v1/playlists/{playlist_id}",
        json={"is_public": True, "is_collaborative": True},
        headers=pro_auth,
    )
    slug = updated.json()["share_slug"]

    friend = bearer(await login(client, 902))
    joined = await client.post(f"/v1/playlists/shared/{slug}/join", headers=friend)
    assert joined.status_code == 200
    assert joined.json()["can_edit"] is True

    added = await client.post(
        f"/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [lib["ids"]["AgADp3"]]},
        headers=friend,
    )
    assert added.status_code == 200
    assert playlist_id in [
        p["id"] for p in (await client.get("/v1/playlists", headers=friend)).json()
    ]
    # A collaborator still cannot rename or delete the playlist.
    assert (
        await client.patch(f"/v1/playlists/{playlist_id}", json={"name": "hijack"}, headers=friend)
    ).status_code == 403
    assert (await client.delete(f"/v1/playlists/{playlist_id}", headers=friend)).status_code == 403


async def test_likes(client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]) -> None:
    ids = lib["ids"]
    liked = await client.put(f"/v1/tracks/{ids['AgADp4']}/like", headers=lib["auth"])  # a duplicate
    assert liked.json() == {"liked": True, "likes_count": 1}
    # Liking the duplicate liked its canonical track.
    again = await client.put(f"/v1/tracks/{ids['AgADp2']}/like", headers=lib["auth"])
    assert again.json()["likes_count"] == 1

    page = (await client.get("/v1/library/likes", headers=lib["auth"])).json()
    assert [t["id"] for t in page["items"]] == [ids["AgADp2"]]
    assert page["items"][0]["liked"] is True

    tracks = (await client.get("/v1/library/tracks", headers=lib["auth"])).json()["items"]
    assert {t["id"]: t["liked"] for t in tracks}[ids["AgADp2"]] is True

    removed = await client.delete(f"/v1/tracks/{ids['AgADp2']}/like", headers=lib["auth"])
    assert removed.json() == {"liked": False, "likes_count": 0}
    assert (await client.get("/v1/library/likes", headers=lib["auth"])).json()["items"] == []


async def test_history_and_resume(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    ids = lib["ids"]
    assert (
        await client.post(
            "/v1/history",
            json={
                "track_id": ids["AgADp1"],
                "duration_played": 200,
                "completed": True,
                "source": "library",
            },
            headers=lib["auth"],
        )
    ).status_code == 204
    # Very short plays are not recorded.
    await client.post(
        "/v1/history",
        json={"track_id": ids["AgADp3"], "duration_played": 2, "source": "search"},
        headers=lib["auth"],
    )
    # A duplicate is recorded against its canonical track.
    await client.post(
        "/v1/history",
        json={
            "track_id": ids["AgADp4"],
            "duration_played": 120,
            "completed": True,
            "source": "playlist",
        },
        headers=lib["auth"],
    )

    recent = (await client.get("/v1/library/recent", headers=lib["auth"])).json()["items"]
    assert [t["id"] for t in recent] == [ids["AgADp2"], ids["AgADp1"]]
    rows = (
        await session.execute(
            text("SELECT track_id, source, completed FROM play_history ORDER BY id")
        )
    ).all()
    assert [(r.source, r.completed) for r in rows] == [("library", True), ("playlist", True)]
    plays = await session.scalar(select(Track.plays_total).where(Track.id == ids["AgADp1"]))
    assert plays == 1

    assert (await client.get("/v1/me/playback", headers=lib["auth"])).json()["track_id"] is None
    saved = await client.put(
        "/v1/me/playback",
        json={
            "track_id": ids["AgADp2"],
            "position_s": 87,
            "queue": [ids["AgADp2"], ids["AgADp1"]],
            "queue_index": 0,
            "shuffle": True,
            "repeat_mode": "all",
            "speed": 1.25,
        },
        headers=lib["auth"],
    )
    assert saved.status_code == 204

    # Another device picks it up.
    resumed = (await client.get("/v1/me/playback", headers=lib["auth"])).json()
    assert resumed["track_id"] == ids["AgADp2"]
    assert resumed["position_s"] == 87
    assert resumed["repeat_mode"] == "all"
    assert [t["id"] for t in resumed["items"]] == [ids["AgADp2"], ids["AgADp1"]]

    bad = await client.put(
        "/v1/me/playback", json={"track_id": ids["AgADp2"], "speed": 9}, headers=lib["auth"]
    )
    assert bad.status_code == 422


async def test_send_to_chat_uses_the_edge_and_stores_the_bot_file_id(
    client: httpx.AsyncClient,
    session: AsyncSession,
    lib: dict[str, Any],
    app_state: Any,
    settings: Any,
) -> None:
    from app.models import EdgeNode

    session.add(EdgeNode(host="cdn.example.test"))
    await session.commit()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"file_id": "CQACnew", "file_unique_id": "AgADnew"})

    original = app_state.http
    app_state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    object.__setattr__(settings, "edge_internal_url", "http://edge.internal:8080")
    try:
        resp = await client.post(f"/v1/tracks/{lib['ids']['AgADp1']}/send", headers=lib["auth"])
    finally:
        await app_state.http.aclose()
        app_state.http = original
        object.__setattr__(settings, "edge_internal_url", "")

    assert resp.status_code == 204
    assert calls[0].url.path == "/internal/send"
    assert calls[0].headers["authorization"] == "Bearer internal-token"
    track = await session.get(Track, lib["ids"]["AgADp1"])
    assert track is not None
    await session.refresh(track)
    assert (track.bot_file_id, track.bot_id) == ("CQACnew", settings.bot_id)


async def test_send_to_chat_without_an_edge_configured(
    client: httpx.AsyncClient, lib: dict[str, Any]
) -> None:
    resp = await client.post(f"/v1/tracks/{lib['ids']['AgADp1']}/send", headers=lib["auth"])
    assert resp.status_code == 503
    assert resp.json()["error"]["details"]["reason"] == "no_edge"


async def test_playlist_query_count_is_flat(
    client: httpx.AsyncClient, lib: dict[str, Any], count_queries: Any
) -> None:
    small = (
        await client.post(
            "/v1/playlists",
            json={"name": "a", "track_ids": [lib["ids"]["AgADp1"]]},
            headers=lib["auth"],
        )
    ).json()["id"]
    big_ids = [lib["ids"]["AgADp1"], lib["ids"]["AgADp2"], lib["ids"]["AgADp3"]]
    big = (
        await client.post(
            "/v1/playlists", json={"name": "b", "track_ids": big_ids}, headers=lib["auth"]
        )
    ).json()["id"]
    await client.get(f"/v1/playlists/{small}", headers=lib["auth"])  # warm caches
    with count_queries() as one:
        await client.get(f"/v1/playlists/{small}", headers=lib["auth"])
    with count_queries() as many:
        await client.get(f"/v1/playlists/{big}", headers=lib["auth"])
    assert many.count == one.count
