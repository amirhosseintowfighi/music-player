"""Phase 3: library browsing, search (real Meilisearch), stream tickets, background jobs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, EdgeNode, Track, User
from app.services import plans
from app.services import search as search_service
from app.services.ingest import ingest_items
from app.services.meili import MeiliClient
from app.workers import jobs
from tests.conftest import bearer, init_data_for, login
from tests.integration.helpers import item, make_channel, subscribe
from tmusic_common.stream_ticket import verify


async def seed_library(session: AsyncSession, user_id: int) -> dict[str, Any]:
    a = await make_channel(session, "chan_alpha", title="Alpha")
    b = await make_channel(session, "chan_beta", title="Beta")
    other = await make_channel(session, "chan_other", title="Other")
    await ingest_items(
        session,
        a,
        [
            item("معین - شب بارونی", msg=1, fuid="AgADmoein"),
            item("Googoosh - Pol", msg=2, fuid="AgADpol", duration=272),
            item("Ebi - Khaneh", msg=3, fuid="AgADebi"),
        ],
    )  # fmt: skip
    await ingest_items(
        session,
        b,
        [
            item("Googoosh - Pol", msg=10, fuid="AgADpol"),  # same file in a second channel
            item("Googoosh - Pol 🎧", msg=11, fuid="AgADpoldup", duration=273),  # re-upload
            item("Queen - Bohemian Rhapsody", msg=12, fuid="AgADqueen", duration=355),
        ],
    )
    await ingest_items(session, other, [item("Dariush - Nooneh Paneer", msg=1, fuid="AgADdariush")])
    await subscribe(session, user_id, a.id, b.id)
    session.add(EdgeNode(host="cdn.example.test"))
    await session.commit()
    ids = {
        fuid: tid
        for fuid, tid in (await session.execute(select(Track.file_unique_id, Track.id))).tuples()
    }
    return {"alpha": a.id, "beta": b.id, "other": other.id, "ids": ids}


@pytest.fixture
async def lib(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    body = await login(client, 801)
    data = await seed_library(session, body["me"]["id"])
    data["auth"] = bearer(body)
    data["user_id"] = body["me"]["id"]
    return data


async def test_library_tracks_dedup_order_and_paging(
    client: httpx.AsyncClient, lib: dict[str, Any]
) -> None:
    resp = await client.get("/v1/library/tracks?limit=2", headers=lib["auth"])
    assert resp.status_code == 200
    page = resp.json()
    ids = lib["ids"]
    # newest first; "Pol" appears once although it is in two channels and re-uploaded.
    assert [t["id"] for t in page["items"]] == [ids["AgADqueen"], ids["AgADpol"]]
    assert page["items"][1]["channels_count"] == 2
    rest = (
        await client.get(
            f"/v1/library/tracks?limit=2&cursor={page['next_cursor']}", headers=lib["auth"]
        )
    ).json()
    assert [t["id"] for t in rest["items"]] == [ids["AgADebi"], ids["AgADmoein"]]
    assert rest["next_cursor"] is None
    assert ids["AgADdariush"] not in [t["id"] for t in page["items"] + rest["items"]]

    track = page["items"][0]
    assert track["title"] == "Bohemian Rhapsody"
    assert track["artists"][0]["name"] == "Queen"
    assert track["language"] == "en"


async def test_library_filters(client: httpx.AsyncClient, lib: dict[str, Any]) -> None:
    only_alpha = (
        await client.get(f"/v1/library/tracks?channel_id={lib['alpha']}", headers=lib["auth"])
    ).json()
    assert {t["id"] for t in only_alpha["items"]} == {
        lib["ids"][f] for f in ("AgADmoein", "AgADpol", "AgADebi")
    }
    long_ones = (
        await client.get("/v1/library/tracks?min_duration=300", headers=lib["auth"])
    ).json()
    assert [t["title"] for t in long_ones["items"]] == ["Bohemian Rhapsody"]
    persian = (await client.get("/v1/library/tracks?language=fa", headers=lib["auth"])).json()
    assert [t["title"] for t in persian["items"]] == ["شب بارونی"]
    artist_id = persian["items"][0]["artists"][0]["id"]
    by_artist = (
        await client.get(f"/v1/library/tracks?artist_id={artist_id}", headers=lib["auth"])
    ).json()
    assert len(by_artist["items"]) == 1
    none = (await client.get("/v1/library/tracks?album=Nope&year=1999", headers=lib["auth"])).json()
    assert none["items"] == []
    bad = await client.get("/v1/library/tracks?language=xx", headers=lib["auth"])
    assert bad.status_code == 422


async def test_library_artists_albums_and_detail_pages(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    artists = (await client.get("/v1/library/artists?limit=1", headers=lib["auth"])).json()
    assert len(artists["items"]) == 1
    more = (
        await client.get(
            f"/v1/library/artists?limit=10&cursor={artists['next_cursor']}", headers=lib["auth"]
        )
    ).json()
    names = {a["name"] for a in artists["items"] + more["items"]}
    assert {"گوگوش", "Queen", "ابی", "معین"} <= names

    await session.execute(
        update(Track)
        .where(Track.id == lib["ids"]["AgADpol"])
        .values(album="Golden Hits", normalized_album="golden hits")
    )
    await session.commit()
    albums = (await client.get("/v1/library/albums", headers=lib["auth"])).json()
    assert albums == [
        {
            "album": "Golden Hits",
            "artist_id": albums[0]["artist_id"],
            "artist_name": "گوگوش",
            "tracks_count": 1,
        }
    ]
    by_album = (
        await client.get("/v1/library/tracks?album=golden%20HITS", headers=lib["auth"])
    ).json()
    assert len(by_album["items"]) == 1

    # English UI shows Latin artist names when known.
    en = await client.patch("/v1/me/lang", json={"lang": "en"}, headers=lib["auth"])
    assert en.status_code == 200
    refreshed = await client.post("/v1/auth/telegram", json={"init_data": init_data_for(801)})
    en_auth = bearer(refreshed.json())
    pol = (await client.get(f"/v1/tracks/{lib['ids']['AgADpol']}", headers=en_auth)).json()
    assert pol["artists"][0]["name"] == "Googoosh"

    artist_id = pol["artists"][0]["id"]
    detail = (await client.get(f"/v1/artists/{artist_id}", headers=en_auth)).json()
    assert detail["name"] == "Googoosh"
    tracks = (await client.get(f"/v1/artists/{artist_id}/tracks", headers=en_auth)).json()
    assert [t["id"] for t in tracks["items"]] == [
        lib["ids"]["AgADpol"]
    ]  # the re-upload is folded in

    # Asking for a duplicate returns its canonical track.
    dup = (await client.get(f"/v1/tracks/{lib['ids']['AgADpoldup']}", headers=en_auth)).json()
    assert dup["id"] == lib["ids"]["AgADpol"]
    assert (await client.get("/v1/tracks/999999", headers=en_auth)).status_code == 404
    assert (await client.get("/v1/artists/999999", headers=en_auth)).status_code == 404

    chan = (await client.get(f"/v1/channels/{lib['beta']}/tracks?limit=2", headers=en_auth)).json()
    assert len(chan["items"]) == 2
    chan2 = (
        await client.get(
            f"/v1/channels/{lib['beta']}/tracks?cursor={chan['next_cursor']}", headers=en_auth
        )
    ).json()
    assert len(chan2["items"]) == 1


async def test_library_query_count_does_not_grow(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any], count_queries: Any
) -> None:
    await client.get("/v1/library/tracks?limit=2", headers=lib["auth"])  # warm caches
    with count_queries() as few:
        await client.get("/v1/library/tracks?limit=2", headers=lib["auth"])
    ch = await session.get(Channel, lib["alpha"])
    await ingest_items(session, ch, [item(f"Singer{i} - Song{i}", msg=100 + i) for i in range(30)])
    await session.commit()
    with count_queries() as many:
        resp = await client.get("/v1/library/tracks?limit=30", headers=lib["auth"])
    assert len(resp.json()["items"]) == 30
    assert many.count == few.count  # no N+1


# ── search ──


async def sync(session: AsyncSession, meili: MeiliClient, redis: Any) -> None:
    orig = search_service.SYNC_LAG_S
    search_service.SYNC_LAG_S = 0
    try:
        await session.execute(text("SELECT pg_sleep(0.01)"))
        while await search_service.sync_changes(session, meili, redis):
            pass
    finally:
        search_service.SYNC_LAG_S = orig
    # Wait for Meilisearch to finish indexing.
    tasks = await meili._request("GET", "/tasks?statuses=enqueued,processing&limit=100")
    for task in tasks["results"]:
        await meili.wait({"taskUid": task["uid"]})


@pytest.mark.usefixtures("need_meili")
async def test_search_finglish_scope_and_filters(
    client: httpx.AsyncClient,
    session: AsyncSession,
    meili: MeiliClient,
    redis: Any,
    lib: dict[str, Any],
) -> None:
    await sync(session, meili, redis)
    ids = lib["ids"]

    async def q(query: str, **params: Any) -> dict[str, Any]:
        resp = await client.get("/v1/search", params={"q": query, **params}, headers=lib["auth"])
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    # Finglish → Persian (via alias and via consonant skeleton).
    assert ids["AgADmoein"] in [t["id"] for t in (await q("moein"))["items"]]
    assert ids["AgADmoein"] in [t["id"] for t in (await q("shabe baroni"))["items"]]
    # Persian query, Persian title; Arabic-keyboard spelling still matches.
    assert ids["AgADmoein"] in [t["id"] for t in (await q("شب بارونى"))["items"]]
    # Typo tolerance.
    assert ids["AgADqueen"] in [t["id"] for t in (await q("bohemain"))["items"]]
    # Latin artist found through its Persian name.
    assert ids["AgADpol"] in [t["id"] for t in (await q("گوگوش"))["items"]]

    # Library scope hides channels the user has not added; global finds them.
    assert (await q("dariush"))["items"] == []
    glob = await q("dariush", scope="global")
    assert [t["id"] for t in glob["items"]] == [ids["AgADdariush"]]
    assert glob["degraded"] is False

    # Duplicates are not indexed twice.
    pol_hits = [t["id"] for t in (await q("pol googoosh"))["items"]]
    assert pol_hits.count(ids["AgADpol"]) == 1
    assert ids["AgADpoldup"] not in pol_hits

    # Filters.
    assert (await q("pol", channel_id=lib["other"]))["items"] == []
    assert [t["id"] for t in (await q("queen", min_duration=300))["items"]] == [ids["AgADqueen"]]
    assert (await q("queen", max_duration=100))["items"] == []
    assert (await q("queen", language="fa"))["items"] == []

    history = (
        await client.get("/v1/search/suggest", params={"q": "mo"}, headers=lib["auth"])
    ).json()
    assert history["history"] == ["moein"]
    assert ids["AgADmoein"] in [t["id"] for t in history["tracks"]]
    assert (await client.delete("/v1/search/history", headers=lib["auth"])).status_code == 204
    assert (await client.get("/v1/search/suggest", headers=lib["auth"])).json() == {
        "history": [],
        "tracks": [],
    }


@pytest.mark.usefixtures("need_meili")
async def test_search_sync_removes_hidden_and_duplicates(
    client: httpx.AsyncClient,
    session: AsyncSession,
    meili: MeiliClient,
    redis: Any,
    lib: dict[str, Any],
) -> None:
    await sync(session, meili, redis)
    await session.execute(
        update(Track).where(Track.id == lib["ids"]["AgADqueen"]).values(hidden=True)
    )
    await session.commit()
    await sync(session, meili, redis)
    resp = await client.get(
        "/v1/search", params={"q": "queen", "scope": "global"}, headers=lib["auth"]
    )
    assert resp.json()["items"] == []

    total = await search_service.full_reindex(session, meili, redis)
    assert total >= 6


async def test_search_falls_back_to_postgres(
    client: httpx.AsyncClient, lib: dict[str, Any], app_state: Any
) -> None:
    app_state.meili = MeiliClient(app_state.http, "http://127.0.0.1:9", "", "tracks")
    resp = await client.get("/v1/search", params={"q": "googoosh"}, headers=lib["auth"])
    body = resp.json()
    assert body["degraded"] is True
    assert [t["id"] for t in body["items"]] == [lib["ids"]["AgADpol"]]
    scoped = await client.get("/v1/search", params={"q": "dariush"}, headers=lib["auth"])
    assert scoped.json()["items"] == []
    glob = await client.get(
        "/v1/search", params={"q": "dariush", "scope": "global"}, headers=lib["auth"]
    )
    assert len(glob.json()["items"]) == 1
    odd = await client.get("/v1/search", params={"q": "%_!"}, headers=lib["auth"])
    assert odd.json()["items"] == []
    ready = (await client.get("/readyz")).json()
    assert ready["search"] is False


async def test_search_with_no_channels(client: httpx.AsyncClient) -> None:
    auth = bearer(await login(client, 802))
    resp = await client.get("/v1/search", params={"q": "anything"}, headers=auth)
    assert resp.json() == {"items": [], "total": 0, "offset": 0, "limit": 20, "degraded": False}
    assert (await client.get("/v1/search", headers=auth)).status_code == 422


def test_meili_filter_building() -> None:
    from app.services.library import TrackFilters

    filters = search_service.meili_filters(
        [1, 2],
        TrackFilters(channel_id=3, artist_id=4, album='A "q"', language="fa", year=2000,
                     min_duration=10, max_duration=20),
    )  # fmt: skip
    assert filters == [
        "channel_ids = 3",
        "channel_ids IN [1, 2]",
        "artist_ids = 4",
        'language = "fa"',
        "year = 2000",
        "duration >= 10",
        "duration <= 20",
        'album_norm = "a q"',
    ]
    queries = search_service.build_queries("x", [])
    assert len(queries) == 1  # skeleton too short to be useful


# ── stream tickets ──


async def test_stream_ticket_sources(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any], settings: Any
) -> None:
    ids = lib["ids"]
    resp = await client.post(f"/v1/tracks/{ids['AgADpoldup']}/stream", headers=lib["auth"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"].startswith(f"https://cdn.example.test/s/{ids['AgADpol']}?t=")
    assert body["thumb_url"] is None
    ticket = verify(body["url"].split("t=", 1)[1], settings.signing_keys)
    assert ticket.track_id == ids["AgADpol"]  # canonical id, so the edge cache is shared
    assert ticket.user_id == lib["user_id"]
    assert ticket.channel_username is not None
    assert ticket.message_id is not None
    assert ticket.bot_file_id is None
    assert ticket.exp <= time.time() + settings.stream_ticket_ttl_s + 1

    # A bot-owned file id (≤ 20 MB) is preferred.
    await session.execute(
        update(Track)
        .where(Track.id == ids["AgADpoldup"])
        .values(bot_file_id="CQAC", bot_id=settings.bot_id, has_thumb=True)
    )
    await session.commit()
    body = (await client.post(f"/v1/tracks/{ids['AgADpol']}/stream", headers=lib["auth"])).json()
    ticket = verify(body["url"].split("t=", 1)[1], settings.signing_keys)
    assert (ticket.bot_file_id, ticket.message_id) == ("CQAC", None)
    assert body["thumb_url"] is not None

    # Too big for the Bot API → MTProto again.
    await session.execute(
        update(Track).where(Track.id == ids["AgADpoldup"]).values(file_size=30 * 1024 * 1024)
    )
    await session.commit()
    body = (await client.post(f"/v1/tracks/{ids['AgADpol']}/stream", headers=lib["auth"])).json()
    assert verify(body["url"].split("t=", 1)[1], settings.signing_keys).bot_file_id is None


async def test_stream_unavailable_cases(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any]
) -> None:
    ids = lib["ids"]
    await session.execute(update(Track).where(Track.id == ids["AgADebi"]).values(hidden=True))
    await session.commit()
    assert (
        await client.post(f"/v1/tracks/{ids['AgADebi']}/stream", headers=lib["auth"])
    ).status_code == 404
    assert (await client.post("/v1/tracks/999999/stream", headers=lib["auth"])).status_code == 404

    # Only source is a channel that is no longer active.
    await session.execute(
        text("UPDATE channels SET status = 'paused' WHERE id = :c").bindparams(c=lib["other"])
    )
    await session.commit()
    resp = await client.post(f"/v1/tracks/{ids['AgADdariush']}/stream", headers=lib["auth"])
    assert resp.status_code == 503
    assert resp.json()["error"]["details"]["reason"] == "no_source"

    from app.services import stream

    await session.execute(update(EdgeNode).values(healthy=False))
    await session.commit()
    stream.clear_edge_cache()
    assert (
        await client.post(f"/v1/tracks/{ids['AgADqueen']}/stream", headers=lib["auth"])
    ).status_code == 503


async def test_free_daily_play_limit(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any], redis: Any
) -> None:
    ids = lib["ids"]
    await session.execute(
        text(
            "UPDATE plans SET limits = jsonb_set(limits, '{daily_plays}', '2') WHERE code = 'free'"
        )
    )
    await session.commit()
    plans.clear_caches()  # admin changes normally propagate within CACHE_TTL_S
    for fuid in ("AgADpol", "AgADqueen", "AgADpol"):  # replaying a track does not count twice
        assert (
            await client.post(f"/v1/tracks/{ids[fuid]}/stream", headers=lib["auth"])
        ).status_code == 200
    over = await client.post(f"/v1/tracks/{ids['AgADebi']}/stream", headers=lib["auth"])
    assert over.status_code == 402
    assert over.json()["error"]["details"] == {"kind": "daily_plays", "limit": 2}

    await session.execute(
        update(User)
        .where(User.id == lib["user_id"])
        .values(plan_code="pro_monthly", premium_until=datetime.now(UTC) + timedelta(days=1))
    )
    await session.commit()
    pro = await client.post("/v1/auth/telegram", json={"init_data": init_data_for(801)})
    resp = await client.post(f"/v1/tracks/{ids['AgADebi']}/stream", headers=bearer(pro.json()))
    assert resp.status_code == 200


def test_edge_base_url() -> None:
    from app.services.stream import edge_base_url

    assert edge_base_url("cdn.example.com") == "https://cdn.example.com"
    assert edge_base_url("http://localhost:8081/") == "http://localhost:8081"


# ── jobs ──


async def test_jobs(
    session: AsyncSession,
    engine: Any,
    meili: MeiliClient,
    redis: Any,
    lib: dict[str, Any],
    meili_available: bool,
) -> None:
    from app.db import make_sessionmaker

    requests: list[str] = []

    def edge_handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(edge_handler)) as http:
        ctx = {
            "sessionmaker": make_sessionmaker(engine),
            "redis_app": redis,
            "meili": meili,
            "http": http,
        }
        await session.execute(
            text(
                "INSERT INTO play_history (user_id, track_id, duration_played, completed, source) "
                "VALUES (:u, :t, 100, true, 'library'), (:u, :t, 50, false, 'library')"
            ).bindparams(u=lib["user_id"], t=lib["ids"]["AgADpol"])
        )
        await session.execute(
            text("INSERT INTO likes (user_id, track_id) VALUES (:u, :t)").bindparams(
                u=lib["user_id"], t=lib["ids"]["AgADqueen"]
            )
        )
        await session.execute(
            update(Track).where(Track.id == lib["ids"]["AgADebi"]).values(plays_7d=9, likes_count=3)
        )
        await session.commit()
        await jobs.refresh_track_counters(ctx)
        session.expire_all()
        counts = dict(
            (await session.execute(select(Track.file_unique_id, Track.plays_7d))).tuples().all()
        )
        likes = dict(
            (await session.execute(select(Track.file_unique_id, Track.likes_count))).tuples().all()
        )
        assert (counts["AgADpol"], counts["AgADebi"]) == (2, 0)
        assert (likes["AgADqueen"], likes["AgADebi"]) == (1, 0)

        await jobs.edge_healthcheck(ctx)
        assert requests == ["https://cdn.example.test/healthz"]
        node = (await session.scalars(select(EdgeNode))).one()
        await session.refresh(node)
        assert node.healthy is False
        assert node.last_check_at is not None

        await jobs.maintenance_daily(ctx)

        if meili_available:
            await redis.set("lock:sync_search", "1")
            assert await jobs.sync_search(ctx) == 0  # another replica holds the lock
            await redis.delete("lock:sync_search")
            await jobs.ensure_search_index(ctx)
            await jobs.reindex_search(ctx)


async def test_thumbnail_batch_does_not_count_plays(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any], redis: Any, settings: Any
) -> None:
    ids = lib["ids"]
    await session.execute(
        update(Track)
        .where(Track.id.in_([ids["AgADpol"], ids["AgADpoldup"]]))
        .values(has_thumb=True)
    )
    await session.execute(
        text(
            "UPDATE plans SET limits = jsonb_set(limits, '{daily_plays}', '1') WHERE code = 'free'"
        )
    )
    await session.commit()
    plans.clear_caches()

    resp = await client.post(
        "/v1/tracks/thumbs",
        json={"ids": [ids["AgADpol"], ids["AgADpoldup"], ids["AgADqueen"], 999999]},
        headers=lib["auth"],
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Both the canonical track and its duplicate resolve to the same canonical artwork.
    assert set(items) == {str(ids["AgADpol"]), str(ids["AgADpoldup"])}
    assert items[str(ids["AgADpol"])] == items[str(ids["AgADpoldup"])]
    url = items[str(ids["AgADpol"])]
    assert url.startswith(f"https://cdn.example.test/t/{ids['AgADpol']}?t=")
    ticket = verify(url.split("t=", 1)[1], settings.signing_keys)
    assert ticket.exp > time.time() + 30 * 60  # long-lived, so lists can be cached

    # The daily limit is untouched: a normal stream request still succeeds.
    assert (
        await client.post(f"/v1/tracks/{ids['AgADqueen']}/stream", headers=lib["auth"])
    ).status_code == 200
    assert (
        await client.post("/v1/tracks/thumbs", json={"ids": []}, headers=lib["auth"])
    ).status_code == 422


async def test_thumbnail_batch_query_count(
    client: httpx.AsyncClient, session: AsyncSession, lib: dict[str, Any], count_queries: Any
) -> None:
    ids = list(lib["ids"].values())
    await session.execute(update(Track).values(has_thumb=True))
    await session.commit()
    await client.post(
        "/v1/tracks/thumbs", json={"ids": ids[:1]}, headers=lib["auth"]
    )  # warm caches
    with count_queries() as counter:
        resp = await client.post("/v1/tracks/thumbs", json={"ids": ids}, headers=lib["auth"])
    assert resp.status_code == 200
    assert counter.count <= 3  # one batch query, regardless of list length
