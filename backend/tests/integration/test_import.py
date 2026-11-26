"""Playlist import: pasted lines and exported CSVs, matched without guessing."""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import importer
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel


def test_lines_are_cleaned_and_split() -> None:
    wanted, skipped = importer.parse(
        "1. Ebi - Khaneh (Official Video) 4:05\n"
        "\n"
        "https://open.spotify.com/track/abc\n"
        "Pol – Googoosh\n"
        "Shabe Barooni by Moein\n"
        "03) Soghati\n"
    )
    assert [(w.first, w.second) for w in wanted] == [
        ("Ebi", "Khaneh"),
        ("Pol", "Googoosh"),
        ("Moein", "Shabe Barooni"),
        ("", "Soghati"),
    ]
    assert skipped == 1


def test_an_exported_csv() -> None:
    body = (
        '"Track URI","Track Name","Artist URI(s)","Artist Name(s)"\n'
        '"spotify:track:1","Khaneh","spotify:artist:1","Ebi;Shadmehr"\n'
    )
    wanted, _ = importer.parse(body)
    assert [(w.first, w.second) for w in wanted] == [("Ebi", "Khaneh")]


async def test_matching_finds_songs_either_way_round_and_skips_strangers(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 1010))
    channel = await make_channel(session, "importchan")
    await ingest_items(
        session,
        channel,
        [
            item("Ebi - Khaneh", msg=1),
            item("Googoosh - Pol", msg=2),
            item("Moein - Shabe Barooni", msg=3),
        ],
    )
    await session.commit()
    resp = await client.post(
        "/v1/playlists/import/match",
        json={"text": "Ebi - Khaneh\nPol - Googoosh\nUnknown Band - Nothing Like It\nebi - khaneh"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = [line["track"]["title"] if line["track"] else None for line in body["lines"]]
    # The repeated line is not added twice.
    assert titles == ["Khaneh", "Pol", None, None]
    assert (body["matched"], body["total"], body["skipped"]) == (2, 4, 0)

    ids = [line["track"]["id"] for line in body["lines"] if line["track"]]
    created = await client.post(
        "/v1/playlists", json={"name": "Imported", "track_ids": ids}, headers=auth
    )
    assert created.status_code == 201
    assert created.json()["tracks_count"] == 2
