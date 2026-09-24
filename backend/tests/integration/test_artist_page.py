"""Artist pages: the songs people liked, the albums, and the photo.

The matching rules are the part worth pinning. A wrong photo on an artist page is
worse than no photo, so a Spotify result is only taken when the names actually
agree — and "looked and found nothing" has to be remembered, or every artist
Spotify has never heard of is looked up again every twenty minutes forever.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import artistinfo
from app.services.ingest import ingest_items
from tests.conftest import bearer, login

from .helpers import item, make_channel


@pytest.fixture(autouse=True)
def no_token_cache() -> Any:
    artistinfo.clear_token_cache()
    yield
    artistinfo.clear_token_cache()


def spotify(items: list[dict[str, Any]], status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if status != 200:
            return httpx.Response(status, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={"artists": {"items": items}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def artist_result(name: str, images: int = 2) -> dict[str, Any]:
    return {
        "id": "sp-1",
        "name": name,
        "popularity": 61,
        "images": [{"url": f"https://i.scdn.co/image/{i}"} for i in range(images)],
    }


def creds() -> Settings:
    from app.config import get_settings

    settings = get_settings()
    return settings.model_copy(
        update={
            "spotify_client_id": "id",
            "spotify_client_secret": type(settings.bot_token)("secret"),
        }
    )


# ── matching ──────────────────────────────────────────────────────────────────


def test_a_name_only_matches_itself() -> None:
    assert artistinfo.matches("Googoosh", "Googoosh")
    assert artistinfo.matches("googoosh", "GOOGOOSH")
    # Same person, written in both scripts.
    assert artistinfo.matches("Moein", "معین")
    # A different person who shares a first name is not the same artist.
    assert not artistinfo.matches("Moein", "Moein Zandi")
    assert not artistinfo.matches("Dariush", "Dariush Eghbali Band")


async def test_a_photo_is_taken_only_when_the_name_agrees(session: AsyncSession) -> None:
    # tracks_count is the queue's order, so this one is picked first whatever else
    # the shared test database happens to hold.
    await session.execute(
        text(
            "INSERT INTO artists (name, normalized_name, latin_name, tracks_count)"
            " VALUES ('معین', 'معین-sp', 'Moein', 9999)"
        )
    )
    await session.commit()

    async with spotify([artist_result("Some Other Guy"), artist_result("Moein")]) as http:
        result = await artistinfo.enrich_batch(session, http, creds(), limit=1)
    await session.commit()

    assert result == {"looked_up": 1, "found": 1}
    row = (
        await session.execute(
            text(
                "SELECT image_url, spotify_id, popularity FROM artists"
                " WHERE normalized_name = 'معین-sp'"
            )
        )
    ).one()
    assert row.image_url == "https://i.scdn.co/image/1"  # the middle size, not the biggest
    assert row.spotify_id == "sp-1"
    assert row.popularity == 61


async def test_an_artist_spotify_never_heard_of_is_not_asked_twice(
    session: AsyncSession,
) -> None:
    await session.execute(
        text(
            "INSERT INTO artists (name, normalized_name, latin_name, tracks_count)"
            " VALUES ('یک خوانندهٔ محلی', 'unknown-sp', 'Yek Khanandeye Mahalli', 9999)"
        )
    )
    await session.commit()

    async with spotify([]) as http:
        first = await artistinfo.enrich_batch(session, http, creds(), limit=1)
        await session.commit()
        asked_at = await session.scalar(
            text("SELECT enriched_at FROM artists WHERE normalized_name = 'unknown-sp'")
        )
        second = await artistinfo.enrich_batch(session, http, creds(), limit=1)
    await session.commit()

    assert first == {"looked_up": 1, "found": 0}
    assert asked_at is not None  # remembered as "asked, nothing there"
    assert second["found"] == 0
    # The same artist is not in the queue any more, whoever the next one is.
    assert (
        await session.scalar(
            text("SELECT image_url FROM artists WHERE normalized_name = 'unknown-sp'")
        )
        is None
    )


async def test_without_credentials_nothing_happens(session: AsyncSession) -> None:
    from app.config import get_settings

    async with spotify([artist_result("Anyone")]) as http:
        assert await artistinfo.enrich_batch(session, http, get_settings()) == {
            "looked_up": 0,
            "found": 0,
        }


async def test_rate_limiting_stops_the_batch_without_marking_anyone(
    session: AsyncSession,
) -> None:
    await session.execute(
        text(
            "INSERT INTO artists (name, normalized_name, latin_name, tracks_count)"
            " VALUES ('rate', 'rate-sp', 'Rate', 9999)"
        )
    )
    await session.commit()

    async with spotify([], status=429) as http:
        assert await artistinfo.enrich_batch(session, http, creds(), limit=1) == {
            "looked_up": 0,
            "found": 0,
        }
    await session.commit()
    pending = await session.scalar(
        text("SELECT enriched_at FROM artists WHERE normalized_name = 'rate-sp'")
    )
    assert pending is None  # still in the queue, not silently consumed


# ── the page itself ───────────────────────────────────────────────────────────


async def test_the_page_shows_the_best_liked_songs_and_the_albums(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "artistchan")
    await ingest_items(
        session,
        channel,
        [
            item("Dariush - Vatan", msg=1, fuid="AgADar1"),
            item("Dariush - Cheshme Man", msg=2, fuid="AgADar2"),
            item("Dariush - Bavar Kon", msg=3, fuid="AgADar3"),
        ],
    )
    await session.commit()
    ids = [
        row[0]
        for row in (
            await session.execute(
                text("SELECT id FROM tracks WHERE file_unique_id LIKE 'AgADar%' ORDER BY id")
            )
        ).all()
    ]
    # The second song is the one people liked; the third one has an album.
    await session.execute(
        text("UPDATE tracks SET likes_count = 40 WHERE id = :id").bindparams(id=ids[1])
    )
    await session.execute(
        text("UPDATE tracks SET album = 'Boodan', year = 1993 WHERE id = :id").bindparams(id=ids[2])
    )
    artist_id = await session.scalar(
        text("SELECT artist_id FROM track_artists WHERE track_id = :id").bindparams(id=ids[0])
    )
    await session.commit()

    auth = bearer(await login(client, 8811))
    page = (await client.get(f"/v1/artists/{artist_id}/page", headers=auth)).json()

    assert page["artist"]["tracks_count"] >= 3
    assert page["top_tracks"][0]["id"] == ids[1]  # most liked first
    assert [album["name"] for album in page["albums"]] == ["Boodan"]
    assert page["albums"][0]["year"] == 1993
    assert page["albums"][0]["tracks"] == 1


# ── finding an artist by typing their name ────────────────────────────────────


async def test_searching_a_name_finds_the_artist_in_every_spelling(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The search box is how people look for a singer, not just for a song title.

    The name it comes back under is the catalogue's, not the one typed: "Googoosh"
    and "گوگوش" are one artist, and the seeded aliases are what make that true.
    """
    channel = await make_channel(session, "searchchan")
    await ingest_items(session, channel, [item("Googoosh - Pol", msg=11, fuid="AgADsr1")])
    await session.commit()
    artist_id = await session.scalar(
        text(
            "SELECT ta.artist_id FROM track_artists ta JOIN tracks t ON t.id = ta.track_id"
            " WHERE t.file_unique_id = 'AgADsr1'"
        )
    )
    auth = bearer(await login(client, 8812))

    async def ids(query: str) -> list[int]:
        resp = await client.get("/v1/search/artists", params={"q": query}, headers=auth)
        assert resp.status_code == 200
        return [row["id"] for row in resp.json()]

    assert artist_id in await ids("Googoosh")
    assert artist_id in await ids("googoosh")  # case is not a different artist
    assert artist_id in await ids("گوگوش")  # neither is the other script
    assert artist_id in await ids("Gogoosh")  # a near miss still finds them
    assert await ids("zzzzznobody") == []


async def test_artist_results_carry_what_the_chip_needs(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "chipchan")
    await ingest_items(session, channel, [item("Hayedeh - Soghati", msg=12, fuid="AgADsr2")])
    await session.commit()
    artist_id = await session.scalar(
        text(
            "SELECT ta.artist_id FROM track_artists ta JOIN tracks t ON t.id = ta.track_id"
            " WHERE t.file_unique_id = 'AgADsr2'"
        )
    )
    auth = bearer(await login(client, 8813))

    rows = (await client.get("/v1/search/artists", params={"q": "Hayedeh"}, headers=auth)).json()
    hit = next(row for row in rows if row["id"] == artist_id)
    assert hit["tracks_count"] >= 1
    assert "image_url" in hit  # null until enrichment; the chip falls back to a glyph
