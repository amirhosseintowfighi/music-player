from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artist, Blacklist, Channel, ChannelTrack, Track, TrackArtist
from app.services.ingest import ingest_items
from tests.integration.helpers import item, make_channel


async def _track(session: AsyncSession, fuid: str) -> Track:
    return (await session.scalars(select(Track).where(Track.file_unique_id == fuid))).one()


async def test_ingest_normalises_and_links_artists(session: AsyncSession) -> None:
    ch = await make_channel(session, "persianhits", title="Persian Hits")
    stats = await ingest_items(
        session,
        ch,
        [
            item("Moein - Shabe Barooni @persianhits", "Persian Hits", fuid="AgADa1"),
            item("Bi Ghararam (feat. Hamed Homayoun)", "Sirvan Khosravi", fuid="AgADa2"),
            item(None, None, fuid="AgADa3", file_name="Unknown_Song.mp3"),
        ],
    )
    await session.commit()
    assert (stats.inserted, stats.updated, stats.duplicates, stats.skipped) == (3, 0, 0, 0)

    moein = await _track(session, "AgADa1")
    assert moein.title == "Shabe Barooni"
    assert moein.performer == "Persian Hits"  # raw tag kept for audit
    # "Moein" resolved to the seeded Persian artist through its alias.
    assert moein.normalized_artist == "معین"
    assert moein.normalized_title == "shabe barooni"
    assert moein.language == "en"
    assert moein.metadata_confidence >= 60

    sirvan = await _track(session, "AgADa2")
    roles = (
        await session.execute(
            select(Artist.normalized_name, TrackArtist.role)
            .join(TrackArtist, TrackArtist.artist_id == Artist.id)
            .where(TrackArtist.track_id == sirvan.id)
            .order_by(TrackArtist.role)
        )
    ).all()
    assert {tuple(r) for r in roles} == {("سیروان خسروی", "primary"), ("حامد همایون", "feature")}
    assert sirvan.title == "Bi Ghararam"

    unknown = await _track(session, "AgADa3")
    assert unknown.title == "Unknown Song"
    assert unknown.metadata_confidence < 60  # lands in the review queue

    await session.refresh(ch)
    assert ch.tracks_count == 3


async def test_same_file_in_many_channels_is_one_track(session: AsyncSession) -> None:
    a = await make_channel(session, "chan_a")
    b = await make_channel(session, "chan_b")
    await ingest_items(session, a, [item("Ebi - Khaneh", fuid="AgADsame", msg=10)])
    stats = await ingest_items(session, b, [item("Ebi - Khaneh", fuid="AgADsame", msg=99)])
    await session.commit()
    assert (stats.inserted, stats.updated) == (0, 1)
    track = await _track(session, "AgADsame")
    assert track.channels_count == 2
    links = (
        await session.scalars(
            select(ChannelTrack.channel_id).where(ChannelTrack.track_id == track.id)
        )
    ).all()
    assert sorted(links) == sorted([a.id, b.id])


async def test_reingest_is_idempotent_and_keeps_edited_metadata(session: AsyncSession) -> None:
    ch = await make_channel(session, "idem")
    batch = [item("Dariush - Nooneh Paneer", fuid="AgADidem", msg=5)]
    await ingest_items(session, ch, batch)
    track = await _track(session, "AgADidem")
    track.title = "Noon o Paneer (edited)"
    await session.flush()
    stats = await ingest_items(session, ch, batch)
    await session.commit()
    assert (stats.inserted, stats.updated) == (0, 1)
    await session.refresh(track)
    assert track.title == "Noon o Paneer (edited)"
    await session.refresh(ch)
    assert ch.tracks_count == 1  # the same message is not counted twice


async def test_fuzzy_duplicates_point_to_the_oldest(session: AsyncSession) -> None:
    ch = await make_channel(session, "fuzzy")
    await ingest_items(session, ch, [item("Googoosh - Pol", fuid="AgADorig", duration=272)])
    stats = await ingest_items(
        session,
        ch,
        [
            item("Googoosh - Pol 🎵", fuid="AgADdup1", duration=273),  # re-upload
            item("GOOGOOSH – Pol", fuid="AgADdup2", duration=270),  # re-upload, −2s
            item("Googoosh - Pol", fuid="AgADfar", duration=280),  # different edit
            item("Googoosh - Hamsafar", fuid="AgADother", duration=272),  # different song
        ],
    )
    await session.commit()
    orig = await _track(session, "AgADorig")
    assert stats.duplicates == 2
    assert (await _track(session, "AgADdup1")).canonical_track_id == orig.id
    assert (await _track(session, "AgADdup2")).canonical_track_id == orig.id
    assert (await _track(session, "AgADfar")).canonical_track_id is None
    assert (await _track(session, "AgADother")).canonical_track_id is None


async def test_dedup_chain_is_flattened_within_one_batch(session: AsyncSession) -> None:
    ch = await make_channel(session, "chain")
    await ingest_items(
        session,
        ch,
        [
            item("Hayedeh - Soghati", fuid="AgADc1"),
            item("Hayedeh - Soghati", fuid="AgADc2"),
            item("Hayedeh - Soghati", fuid="AgADc3"),
        ],
    )
    await session.commit()
    root = await _track(session, "AgADc1")
    assert root.canonical_track_id is None
    for fuid in ("AgADc2", "AgADc3"):
        assert (await _track(session, fuid)).canonical_track_id == root.id


async def test_unknown_artist_needs_near_exact_title(session: AsyncSession) -> None:
    ch = await make_channel(session, "noartist")
    await ingest_items(
        session,
        ch,
        [item("Instrumental Rain", fuid="AgADn1"), item("Instrumental Rain 2", fuid="AgADn2")],
    )
    await session.commit()
    assert (await _track(session, "AgADn2")).canonical_track_id is None


async def test_blacklisted_file_is_skipped_and_hidden_propagates(session: AsyncSession) -> None:
    ch = await make_channel(session, "takedown")
    session.add(Blacklist(entity_type="track", value="AgADbanned", reason="copyright"))
    await ingest_items(session, ch, [item("Andy - Kochaye", fuid="AgADhidden")])
    hidden = await _track(session, "AgADhidden")
    hidden.hidden = True
    await session.flush()

    stats = await ingest_items(
        session,
        ch,
        [item("Andy - Kochaye", fuid="AgADbanned"), item("Andy - Kochaye", fuid="AgADreupload")],
    )
    await session.commit()
    assert stats.skipped == 1
    assert (
        await session.scalar(select(Track.id).where(Track.file_unique_id == "AgADbanned"))
    ) is None
    reupload = await _track(session, "AgADreupload")
    assert reupload.hidden
    assert reupload.hidden_reason == "duplicate_of_hidden"


async def test_bot_items_store_the_bot_file_id(session: AsyncSession) -> None:
    ch = await make_channel(session, "botch", source="bot_admin")
    await ingest_items(
        session, ch, [item("X - Y", fuid="AgADbot", bot_file_id="CQAC")], bot_id=123456
    )
    mt = await make_channel(session, "mtch")
    # A later MTProto sighting (no file id) must not erase the bot's file id.
    await ingest_items(session, mt, [item("X - Y", fuid="AgADbot")])
    await session.commit()
    track = await _track(session, "AgADbot")
    assert (track.bot_file_id, track.bot_id) == ("CQAC", 123456)
    assert track.bot_file_id_updated_at is not None


async def test_merged_artist_resolves_to_target(session: AsyncSession) -> None:
    ch = await make_channel(session, "merged")
    target = (await session.scalars(select(Artist).where(Artist.normalized_name == "ابی"))).one()
    session.add(
        Artist(name="Ebi Official", normalized_name="ebi official", merged_into_id=target.id)
    )
    await session.flush()
    await ingest_items(session, ch, [item("Track", "Ebi Official", fuid="AgADmerge")])
    await session.commit()
    track = await _track(session, "AgADmerge")
    assert track.normalized_artist == "ابی"


async def test_ingest_query_count_is_constant(session: AsyncSession, count_queries: Any) -> None:
    ch = await make_channel(session, "perf")
    await ingest_items(session, ch, [item("Warm - Up", fuid="AgADwarm")])  # loads flag cache
    await session.commit()
    small = [item(f"Artist {i} - Song {i}", fuid=f"AgADqs{i}") for i in range(3)]
    large = [item(f"Singer {i} - Tune {i}", fuid=f"AgADql{i}") for i in range(60)]
    with count_queries() as q_small:
        await ingest_items(session, ch, small)
    with count_queries() as q_large:
        await ingest_items(session, ch, large)
    await session.commit()
    assert q_large.count == q_small.count  # set-based: no per-item queries
    assert q_large.count <= 16


async def test_empty_batch(session: AsyncSession) -> None:
    ch = await make_channel(session, "empty")
    stats = await ingest_items(session, ch, [])
    assert stats.inserted == 0
    count = await session.scalar(text("SELECT count(*) FROM tracks"))
    assert count == 0
    assert (await session.get(Channel, ch.id)) is not None
