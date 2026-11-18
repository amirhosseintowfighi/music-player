"""Search by lyrics: the words of a song find it, and the results say so (0017)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import AppState
from app.config import Settings
from app.models import Track, TrackLyrics
from app.services import lyrics, search
from app.services.ingest import ingest_items
from app.services.meili import LYRICS_FIELDS
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel, subscribe

# What search returns, not the engine behind it: see the Meilisearch test below.
pytestmark = pytest.mark.usefixtures("postgres_search")

LRC = "[ar: Ebi]\n[00:01.00] Shabe Eshgh o Mahtab\n[00:05.50] Dele man dar havaye to"


def test_search_text_drops_timestamps_and_normalises() -> None:
    assert lyrics.search_text(LRC, None) == "shabe eshgh o mahtab dele man dar havaye to"
    assert lyrics.search_text(None, "Plain WORDS") == "plain words"
    assert lyrics.search_text(None, None) is None


def test_only_a_line_asks_the_lyrics_index() -> None:
    assert search.wants_lyrics("dele man dar")
    assert not search.wants_lyrics("googoosh pol")
    queries = search.build_queries("dele man dar havaye", [])
    assert queries[-1]["attributesToSearchOn"] == LYRICS_FIELDS
    assert queries[-1]["matchingStrategy"] == "all"
    assert all(q["attributesToSearchOn"] != LYRICS_FIELDS for q in search.build_queries("pol", []))


@pytest.fixture
async def songs(session: AsyncSession) -> dict[str, int]:
    channel = await make_channel(session, "lyricchan")
    await ingest_items(
        session,
        channel,
        [
            item("Ebi - Khaneh", msg=1, fuid="AgADly1", duration=230),
            item("Googoosh - Pol", msg=2, fuid="AgADly2", duration=200),
        ],
    )
    await session.commit()
    return {
        title: tid for title, tid in (await session.execute(select(Track.title, Track.id))).tuples()
    }


async def test_a_line_of_the_song_finds_it(
    client: httpx.AsyncClient, session: AsyncSession, songs: dict[str, int]
) -> None:
    session.add(
        TrackLyrics(
            track_id=songs["Khaneh"],
            found=True,
            synced=LRC,
            search_text=lyrics.search_text(LRC, None),
        )
    )
    await session.commit()
    tokens = await login(client, 7301)
    user_id = await session.scalar(text("SELECT id FROM users WHERE tg_id = 7301"))
    channel_id = await session.scalar(text("SELECT id FROM channels WHERE username = 'lyricchan'"))
    await subscribe(session, int(user_id), int(channel_id))
    await session.commit()

    resp = await client.get(
        "/v1/search", params={"q": "dele man dar havaye"}, headers=bearer(tokens)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [t["id"] for t in body["items"]] == [songs["Khaneh"]]
    assert body["lyric_matches"] == [songs["Khaneh"]]

    # A title match is a title match, not a lyrics one.
    body = (await client.get("/v1/search", params={"q": "pol"}, headers=bearer(tokens))).json()
    assert [t["id"] for t in body["items"]] == [songs["Pol"]]
    assert body["lyric_matches"] == []


async def test_backfill_fetches_most_played_first_and_rekeys_old_rows(
    session: AsyncSession, app_state: AppState, settings: Settings, songs: dict[str, int]
) -> None:
    # A row stored before 0017, with words but no search key yet.
    session.add(TrackLyrics(track_id=songs["Pol"], found=True, plain="Pol e Ma"))
    await session.execute(
        text("UPDATE tracks SET plays_total = 50 WHERE id = :t").bindparams(t=songs["Khaneh"])
    )
    await session.commit()
    asked: list[str] = []

    def lrclib(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/get":
            return httpx.Response(200, json={"duration": 230, "syncedLyrics": LRC})
        return httpx.Response(200, json=[])

    http = httpx.AsyncClient(transport=httpx.MockTransport(lrclib))
    stats: dict[str, Any] = await lyrics.backfill(session, http, settings, pause_s=0)
    await session.commit()
    assert stats == {"looked_up": 1, "found": 1, "rekeyed": 1}
    rows = {r.track_id: r.search_text for r in (await session.scalars(select(TrackLyrics))).all()}
    assert rows[songs["Pol"]] == "pol e ma"
    assert rows[songs["Khaneh"]] == lyrics.search_text(LRC, None)
    # Nothing left to look up: the next run is free.
    again = await lyrics.backfill(session, http, settings, pause_s=0)
    assert again == {"looked_up": 0, "found": 0, "rekeyed": 0}


# ── loudness: measured by listeners, settled by a few ─────────────────────────


async def test_loudness_is_a_running_mean_of_distinct_listeners(
    client: httpx.AsyncClient, session: AsyncSession, songs: dict[str, int]
) -> None:
    track = songs["Pol"]
    first = bearer(await login(client, 7401))
    url = f"/v1/tracks/{track}/loudness"
    assert (
        await client.put(url, json={"db": -10, "seconds": 60}, headers=first)
    ).status_code == 204
    # The same listener again does not move it.
    await client.put(url, json={"db": -40, "seconds": 60}, headers=first)
    second = bearer(await login(client, 7402))
    await client.put(url, json={"db": -14, "seconds": 60}, headers=second)
    row = (
        await session.execute(
            select(Track.loudness_db, Track.loudness_reports).where(Track.id == track)
        )
    ).one()
    assert (round(row.loudness_db, 1), row.loudness_reports) == (-12.0, 2)

    # Out-of-range values are refused outright.
    bad = await client.put(url, json={"db": 6, "seconds": 60}, headers=second)
    assert bad.status_code == 422
    # And the level travels with the track.
    channel_id = await session.scalar(text("SELECT id FROM channels WHERE username = 'lyricchan'"))
    user_id = await session.scalar(text("SELECT id FROM users WHERE tg_id = 7401"))
    await subscribe(session, int(user_id), int(channel_id))
    await session.commit()
    body = (await client.get("/v1/search", params={"q": "pol"}, headers=first)).json()
    assert body["items"][0]["loudness"] == -12.0


# ── AutoMix analysis: measured by listeners, the steadiest one kept ───────────

MIX = {"v": 1, "bpm": 124.0, "beat": 0.42, "downbeat": 0.42, "key": 7, "intro": 15.9,
       "outro": 170.2, "confidence": 0.6}  # fmt: skip


async def test_mix_analysis_keeps_the_steadiest_and_travels_with_the_track(
    client: httpx.AsyncClient, session: AsyncSession, songs: dict[str, int]
) -> None:
    track = songs["Pol"]
    url = f"/v1/tracks/{track}/mix"
    a = bearer(await login(client, 7501))
    b = bearer(await login(client, 7502))
    c = bearer(await login(client, 7503))
    assert (await client.put(url, json=MIX, headers=a)).status_code == 204
    # Shakier than what we have: ignored.
    await client.put(url, json={**MIX, "bpm": 90.0, "confidence": 0.2}, headers=b)
    # Steadier: replaces it.
    await client.put(url, json={**MIX, "bpm": 125.0, "confidence": 0.9}, headers=c)
    stored = await session.scalar(select(Track.mix_analysis).where(Track.id == track))
    assert stored is not None and stored["bpm"] == 125.0

    # Nonsense is refused.
    bad = await client.put(url, json={**MIX, "intro": 200, "outro": 10}, headers=a)
    assert bad.status_code in (400, 422)
    assert (await client.put(url, json={**MIX, "bpm": 400}, headers=a)).status_code == 422

    channel_id = await session.scalar(text("SELECT id FROM channels WHERE username = 'lyricchan'"))
    user_id = await session.scalar(text("SELECT id FROM users WHERE tg_id = 7501"))
    await subscribe(session, int(user_id), int(channel_id))
    await session.commit()
    body = (await client.get("/v1/search", params={"q": "pol"}, headers=a)).json()
    assert body["items"][0]["mix"]["bpm"] == 125.0
    assert body["items"][0]["mix"]["key"] == 7
