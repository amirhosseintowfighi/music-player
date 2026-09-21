"""Phase 13: the metadata review queue — the loop that fixes what the parser missed.

The queue is only worth having if a correction is cheap and can be applied to a
pattern, so that is what these tests check: what lands in it, and what one fix does.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track
from app.services.ingest import ingest_items
from tests.conftest import bearer, login
from tests.integration.helpers import item, make_channel
from tests.integration.test_admin import admin_token, make_admin
from tmusic_common.indexer_contract import AudioItem


async def owner(
    client: httpx.AsyncClient, session: AsyncSession, tg_id: int = 79001
) -> dict[str, str]:
    await make_admin(session, tg_id)
    await session.commit()
    return await admin_token(client, tg_id)


async def test_the_queue_holds_what_the_parser_doubted_and_what_users_reported(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "reviewch", status="active", title="Review Channel")
    # A title with nothing to go on: the parser says so with a low confidence.
    await ingest_items(session, channel, [item("Track 07", None, msg=7001)], bot_id=None)
    # And a perfectly parsed one, which must NOT be in the queue …
    await ingest_items(session, channel, [item("Pol", "Googoosh", msg=7002)], bot_id=None)
    await session.commit()

    unsure_id = int(await session.scalar(select(Track.id).where(Track.title == "Track 07")) or 0)
    good_id = int(await session.scalar(select(Track.id).where(Track.title == "Pol")) or 0)
    assert unsure_id and good_id

    headers = await owner(client, session)
    queue = (await client.get("/admin/metadata/queue", headers=headers)).json()
    assert [row["id"] for row in queue] == [unsure_id]
    assert queue[0]["metadata_confidence"] < 60
    assert queue[0]["channel_title"] == "Review Channel"

    # … until somebody reports it, which is the other way in.
    auth = bearer(await login(client, 79100))
    await client.post(
        f"/v1/tracks/{good_id}/report",
        json={"reason": "wrong_metadata", "details": "خواننده غلط"},
        headers=auth,
    )
    with_report = (await client.get("/admin/metadata/queue", headers=headers)).json()
    assert next(row["id"] for row in with_report) == good_id  # reported first
    assert with_report[0]["reports"] == 1

    only_reported = (
        await client.get("/admin/metadata/queue?source=reported", headers=headers)
    ).json()
    assert [row["id"] for row in only_reported] == [good_id]


async def test_one_fix_can_correct_every_track_with_the_same_wrong_artist(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Channels post the same mangled name hundreds of times; fixing it once matters."""
    channel = await make_channel(session, "bulkch", status="active")
    await ingest_items(
        session,
        channel,
        [item(f"Song {i}", "Moien Official", msg=7100 + i) for i in range(4)],
        bot_id=None,
    )
    await session.commit()
    ids = list(await session.scalars(select(Track.id).order_by(Track.id)))
    headers = await owner(client, session)

    resp = await client.post(
        f"/admin/metadata/{ids[0]}/fix",
        json={"artist": "معین", "apply_to_artist": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tracks": 4}

    session.expire_all()
    names = (
        await session.execute(
            text(
                "SELECT DISTINCT a.name FROM track_artists ta"
                " JOIN artists a ON a.id = ta.artist_id"
                " WHERE ta.track_id = ANY(:ids) AND ta.role = 'primary'"
            ).bindparams(ids=ids)
        )
    ).scalars()
    assert list(names) == ["معین"]
    confidences = list(await session.scalars(select(Track.metadata_confidence)))
    assert set(confidences) == {100}  # nothing is left in the queue afterwards


async def test_fixing_one_track_leaves_its_neighbours_alone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "singlech", status="active")
    await ingest_items(
        session,
        channel,
        [item("Song A", "Mangled Name", msg=7200), item("Song B", "Mangled Name", msg=7201)],
        bot_id=None,
    )
    await session.commit()
    ids = list(await session.scalars(select(Track.id).order_by(Track.id)))
    headers = await owner(client, session)

    resp = await client.post(
        f"/admin/metadata/{ids[0]}/fix",
        json={"title": "آهنگ الف", "artist": "هایده"},
        headers=headers,
    )
    assert resp.json() == {"tracks": 1}

    session.expire_all()
    first = (await session.scalars(select(Track).where(Track.id == ids[0]))).one()
    second = (await session.scalars(select(Track).where(Track.id == ids[1]))).one()
    assert first.title == "آهنگ الف"
    assert second.title == "Song B"  # untouched


async def test_a_fix_closes_the_report_and_is_audited(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    channel = await make_channel(session, "auditch", status="active")
    await ingest_items(session, channel, [item("Song", "Wrong", msg=7300)], bot_id=None)
    await session.commit()
    track_id = int(await session.scalar(select(Track.id)) or 0)

    auth = bearer(await login(client, 79101))
    await client.post(
        f"/v1/tracks/{track_id}/report", json={"reason": "wrong_metadata"}, headers=auth
    )
    headers = await owner(client, session)
    await client.post(f"/admin/metadata/{track_id}/fix", json={"artist": "معین"}, headers=headers)

    session.expire_all()
    status = await session.scalar(
        text("SELECT status FROM reports WHERE entity_id = :i").bindparams(i=track_id)
    )
    assert status == "actioned"
    audited = await session.scalar(
        text("SELECT count(*) FROM audit_log WHERE action = 'metadata.fix'")
    )
    assert audited == 1


async def test_the_queue_needs_permission(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_admin(session, 79002, role="support", permissions=["dashboard.view"])
    await session.commit()
    weak = await admin_token(client, 79002)

    assert (await client.get("/admin/metadata/queue", headers=weak)).status_code == 403
    assert (
        await client.post("/admin/metadata/1/fix", json={"artist": "x"}, headers=weak)
    ).status_code == 403
    assert (await client.get("/admin/metadata/queue")).status_code == 401


async def test_crawled_tracks_with_no_artist_still_carry_their_channel(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The display fallback (ADR-003 phase 13) needs the channel, not an invented row."""
    from tests.integration.helpers import BASE_TIME, subscribe

    channel = await make_channel(session, "nameless", status="active", title="Nameless Hits")
    await ingest_items(
        session,
        channel,
        [
            AudioItem(
                message_id=7400,
                posted_at=BASE_TIME,
                duration=0,
                file_size=0,
                title="بی کلام",
            )
        ],
    )
    auth = bearer(await login(client, 79102))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await subscribe(session, user_id, channel.id)
    await session.commit()

    track = (await client.get("/v1/library/tracks", headers=auth)).json()["items"][0]
    assert track["artists"] == []  # no artist was invented
    assert track["channel"]["title"] == "Nameless Hits"  # and the UI has something true
