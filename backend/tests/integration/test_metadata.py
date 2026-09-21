"""ID3 backfill on the core side: pick candidates, fill only the gaps, never re-ask."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import metadata
from app.services.ingest import ingest_items
from tests.integration.helpers import item, make_channel


async def seed(session: AsyncSession, count: int = 3) -> list[int]:
    channel = await make_channel(session, "tagch")
    await ingest_items(
        session,
        channel,
        [item(f"ترک {i}", "معین", msg=9500 + i, file_size=5_000_000) for i in range(count)],
        bot_id=None,
    )
    rows = await session.execute(text("SELECT id FROM tracks ORDER BY id"))
    return [row[0] for row in rows]


def edge(responses: dict[int, dict[str, Any]], seen: list[int]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/probe"
        assert request.headers["authorization"].startswith("Bearer ")
        # The ticket is opaque here; the edge verifies it. We answer in call order.
        track_id = sorted(responses)[len(seen) % len(responses)]
        seen.append(track_id)
        return httpx.Response(200, json={"track_id": track_id, **responses[track_id]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_candidates_skip_tracks_that_are_already_complete(
    session: AsyncSession,
) -> None:
    ids = await seed(session)
    assert await metadata.candidates(session) == ids

    await session.execute(
        text("UPDATE tracks SET album = 'A', year = 2000, genre = 'Pop' WHERE id = :id").bindparams(
            id=ids[0]
        )
    )
    assert ids[0] not in await metadata.candidates(session)


async def test_a_probed_track_is_never_asked_again(session: AsyncSession) -> None:
    ids = await seed(session)
    await metadata.mark_probed(session, [ids[0]])
    assert ids[0] not in await metadata.candidates(session)


async def test_apply_fills_gaps_without_overwriting(session: AsyncSession) -> None:
    ids = await seed(session, 1)
    await session.execute(
        text("UPDATE tracks SET album = 'Existing' WHERE id = :id").bindparams(id=ids[0])
    )

    changed = await metadata.apply(
        session, metadata.Probed(track_id=ids[0], album="From Tag", year=1998, genre="Pop")
    )
    assert changed is True

    row = await session.execute(
        text("SELECT album, year, genre, metadata_probed_at FROM tracks WHERE id = :id").bindparams(
            id=ids[0]
        )
    )
    album, year, genre, probed_at = row.one()
    assert album == "Existing"  # a tag never overwrites what we already knew
    assert (year, genre) == (1998, "Pop")
    assert probed_at is not None


async def test_probe_batch_walks_the_edge_and_records_results(
    session: AsyncSession, settings: Any
) -> None:
    ids = await seed(session, 2)
    configured = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    # The tracks need a source the ticket builder can address.
    await session.execute(text("UPDATE channels SET status = 'active'"))

    seen: list[int] = []
    async with edge(
        {
            ids[0]: {"album": "Album A", "year": 1999, "genre": "Pop"},
            ids[1]: {"album": None, "year": None, "genre": None},
        },
        seen,
    ) as http:
        result = await metadata.probe_batch(session, http, configured)

    assert result["probed"] == 2
    assert result["filled"] == 1  # the untagged track counts as probed, not filled
    rows = await session.execute(
        text("SELECT count(*) FROM tracks WHERE metadata_probed_at IS NOT NULL")
    )
    assert rows.scalar_one() == 2
    assert await metadata.candidates(session) == []


async def test_a_failing_edge_does_not_mark_tracks_as_done(
    session: AsyncSession, settings: Any
) -> None:
    await seed(session, 1)
    configured = settings.model_copy(update={"edge_internal_url": "http://edge.test"})
    await session.execute(text("UPDATE channels SET status = 'active'"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "no account"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await metadata.probe_batch(session, http, configured)

    assert result == {"probed": 0, "filled": 0, "failed": 1}
    # Still a candidate: a failed probe must be retried later.
    assert len(await metadata.candidates(session)) == 1


async def test_nothing_to_do_is_cheap(session: AsyncSession, settings: Any) -> None:
    async with httpx.AsyncClient() as http:
        assert await metadata.probe_batch(session, http, settings) == {
            "probed": 0,
            "filled": 0,
            "failed": 0,
        }
