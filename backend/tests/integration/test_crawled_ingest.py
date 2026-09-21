"""Phase 1: a crawled item (no file_unique_id) goes through the *same* ingest.

The point of the migration is that normalisation, artist parsing and dedup never
learn where an item came from. These tests check exactly that: identical metadata
arriving without a file id must produce the same rows, the same artists and the same
canonical grouping — only the identity column and the resolve state differ.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Track
from app.services.ingest import ingest_items, source_key
from tests.integration.helpers import BASE_TIME, item, make_channel
from tmusic_common.indexer_contract import AudioItem

_ids = iter(range(10_000, 99_000))


def crawled(
    title: str | None,
    performer: str | None = None,
    *,
    msg: int | None = None,
    duration: int = 200,
    file_size: int = 4_000_000,
    cdn_url: str | None = None,
    **extra: Any,
) -> AudioItem:
    """An item exactly as the crawler will build it: no file_unique_id, a CDN link."""
    message_id = msg if msg is not None else next(_ids)
    return AudioItem(
        message_id=message_id,
        posted_at=BASE_TIME + timedelta(minutes=message_id),
        duration=duration,
        file_size=file_size,
        mime_type="audio/mpeg",
        title=title,
        performer=performer,
        cdn_url=cdn_url or f"https://cdn.telegram.org/file/{message_id}.mp3",
        **extra,
    )


async def row(session: AsyncSession, track_id: int) -> Any:
    result = await session.execute(
        text(
            "SELECT file_unique_id, source_key, cdn_url, resolve_status, resolved_at,"
            " normalized_title, normalized_artist FROM tracks WHERE id = :id"
        ).bindparams(id=track_id)
    )
    return result.one()


async def test_a_crawled_track_is_stored_unresolved_with_its_cdn_link(
    session: AsyncSession,
) -> None:
    channel = await make_channel(session, "crawled1")
    stats = await ingest_items(session, channel, [crawled("شب بارونی", "معین", msg=10)])

    assert stats.inserted == 1
    track_id = stats.new_track_ids[0]
    stored = await row(session, track_id)

    assert stored.file_unique_id is None
    assert stored.source_key == source_key(channel.id, 10)
    assert stored.cdn_url.endswith("10.mp3")
    assert stored.resolve_status == "unresolved"
    assert stored.resolved_at is None


async def test_normalisation_and_artists_behave_identically_to_the_mtproto_path(
    session: AsyncSession,
) -> None:
    """Same metadata, different transport: the parsed result must match exactly."""
    web_channel = await make_channel(session, "webch")
    mt_channel = await make_channel(session, "mtch")
    title, performer = "Shabe Barooni - معین  [@SomeAdChannel]", "مـعـیـن"

    web = await ingest_items(session, web_channel, [crawled(title, performer, msg=20)])
    mt = await ingest_items(
        session, mt_channel, [item(title, performer, msg=21, fuid="AgADmtproto01")]
    )

    web_row = await row(session, web.new_track_ids[0])
    mt_row = await row(session, mt.new_track_ids[0])
    assert web_row.normalized_title == mt_row.normalized_title
    assert web_row.normalized_artist == mt_row.normalized_artist

    # And the same artist row was reused, not duplicated.
    artists = await session.execute(
        text("SELECT count(*) FROM artists WHERE normalized_name = :n").bindparams(
            n=mt_row.normalized_artist
        )
    )
    assert artists.scalar_one() == 1


async def test_re_crawling_updates_the_same_row_and_refreshes_the_cdn_link(
    session: AsyncSession,
) -> None:
    channel = await make_channel(session, "crawled2")
    first = await ingest_items(
        session, channel, [crawled("آهنگ", "گوگوش", msg=30, cdn_url="https://cdn/old.mp3")]
    )
    second = await ingest_items(
        session, channel, [crawled("آهنگ", "گوگوش", msg=30, cdn_url="https://cdn/new.mp3")]
    )

    assert first.inserted == 1
    assert second.inserted == 0  # the same message is the same track
    assert second.updated == 1
    stored = await row(session, first.new_track_ids[0])
    assert stored.cdn_url == "https://cdn/new.mp3"

    total = await session.execute(text("SELECT count(*) FROM tracks"))
    assert total.scalar_one() == 1


async def test_the_same_song_in_two_channels_is_still_deduplicated(
    session: AsyncSession,
) -> None:
    """Without file_unique_id, the existing fuzzy dedup is what groups them."""
    first_channel = await make_channel(session, "crawled3")
    second_channel = await make_channel(session, "crawled4")

    a = await ingest_items(
        session, first_channel, [crawled("گل سنگم", "هایده", msg=40, duration=250)]
    )
    b = await ingest_items(
        session,
        second_channel,
        # A different upload of the same song: a second longer, a different size.
        [crawled("گل سنگم ", "هایده", msg=41, duration=251, file_size=4_100_000)],
    )

    canonical = await session.execute(
        text("SELECT canonical_track_id FROM tracks WHERE id = :id").bindparams(
            id=b.new_track_ids[0]
        )
    )
    assert canonical.scalar_one() == a.new_track_ids[0]


async def test_a_blacklisted_file_is_still_skipped_when_others_are_crawled(
    session: AsyncSession,
) -> None:
    """A mixed batch must not lose the blacklist just because some items lack ids."""
    channel = await make_channel(session, "crawled5")
    await session.execute(
        text(
            "INSERT INTO blacklist (entity_type, value, reason)"
            " VALUES ('track', 'AgADbanned001', 'dmca')"
        )
    )
    stats = await ingest_items(
        session,
        channel,
        [
            item("ممنوع", "کسی", msg=50, fuid="AgADbanned001"),
            crawled("مجاز", "کسی", msg=51),
        ],
    )
    assert stats.skipped == 1
    assert stats.inserted == 1


async def test_an_mtproto_item_is_still_resolved_on_arrival(session: AsyncSession) -> None:
    channel = await make_channel(session, "crawled6")
    stats = await ingest_items(session, channel, [item("آهنگ", "داریوش", msg=60)])
    stored = await row(session, stats.new_track_ids[0])

    assert stored.file_unique_id is not None
    assert stored.source_key is None
    assert stored.resolve_status == "resolved"
    assert stored.resolved_at is not None


async def test_every_track_is_addressable(session: AsyncSession) -> None:
    """The CHECK constraint: a row with neither identity must be impossible."""
    session.add(
        Track(
            file_unique_id=None,
            source_key=None,
            title="ناشناس",
            duration=1,
            file_size=1,
            normalized_title="x",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_channel_crawl_defaults_are_sane(session: AsyncSession) -> None:
    channel = await make_channel(session, "crawled7")
    stored = await session.execute(
        text(
            "SELECT source_type, crawl_status, crawl_interval_sec, next_crawl_at,"
            " preview_available FROM channels WHERE id = :id"
        ).bindparams(id=channel.id)
    )
    source_type, status, interval, next_at, preview = stored.one()
    assert source_type == "web_preview"
    assert status == "idle"
    assert interval == 3600
    assert next_at <= datetime.now(UTC)  # due immediately
    assert preview is None  # unknown until the first crawl
