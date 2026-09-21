"""Phase 4: how a channel gets into the catalogue, and how it is kept out.

Three sources (seed, user, crawl mention), one queue, and a rejection that is
permanent — a rejected username must never come back as a suggestion.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden
from app.models import Blacklist, Channel, ChannelCandidate, UserChannel
from app.services import discovery
from tests.conftest import bearer, login
from tests.integration.helpers import make_channel
from tests.integration.test_admin import admin_token, make_admin


async def admin_client(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, str]:
    """An owner token, exactly the way the other admin tests get one."""
    await make_admin(session, 77001)
    await session.commit()
    return await admin_token(client, 77001)


# ── scoring ───────────────────────────────────────────────────────────────────


def test_score_prefers_music_channels_people_asked_for() -> None:
    big_but_mixed = discovery.score_of(
        tracks_estimate=5_000, audio_ratio=0.1, posts_per_day=3, requesters=0, mentions=1
    )
    small_but_music = discovery.score_of(
        tracks_estimate=400, audio_ratio=0.95, posts_per_day=8, requesters=3, mentions=1
    )
    assert small_but_music > big_but_mixed

    # Demand breaks ties between otherwise identical channels.
    quiet = discovery.score_of(
        tracks_estimate=400, audio_ratio=0.9, posts_per_day=2, requesters=0, mentions=1
    )
    wanted = discovery.score_of(
        tracks_estimate=400, audio_ratio=0.9, posts_per_day=2, requesters=4, mentions=3
    )
    assert wanted > quiet
    assert (
        discovery.score_of(
            tracks_estimate=10**9, audio_ratio=1, posts_per_day=999, requesters=99, mentions=99
        )
        == 100.0
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@MusicChan", "musicchan"),
        ("musicchan", "musicchan"),
        ("https://t.me/MusicChan", "musicchan"),
        ("https://t.me/s/MusicChan", "musicchan"),
        ("t.me/musicchan/1234", "musicchan"),
        ("https://t.me/+AbCdEf", None),  # private invite link
        ("not a channel!", None),
        ("", None),
    ],
)
def test_clean_username(raw: str, expected: str | None) -> None:
    assert discovery.clean_username(raw) == expected


# ── the three sources ─────────────────────────────────────────────────────────


async def test_a_mention_becomes_a_candidate_and_repeats_raise_its_score(
    session: AsyncSession,
) -> None:
    source = await make_channel(session, "source_chan")
    first = await discovery.record_mentions(
        session, ["@Suggested", "suggested", "source_chan"], discovered_from=source.id
    )
    assert first == 1  # deduplicated, and a known channel is not a candidate

    candidate = (
        await session.scalars(
            select(ChannelCandidate).where(ChannelCandidate.username == "suggested")
        )
    ).one()
    assert (candidate.source, candidate.status, candidate.mention_count) == (
        "crawl_mention",
        "pending",
        1,
    )
    first_score = candidate.score

    await discovery.record_mentions(session, ["suggested"], discovered_from=source.id)
    await session.refresh(candidate)
    assert candidate.mention_count == 2
    assert candidate.score > first_score


async def test_a_user_asking_for_an_indexed_channel_just_gets_it(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Indexed once for the platform: no job, no wait, no duplicate channel row."""
    channel = await make_channel(session, "already", status="active")
    await session.commit()
    auth = bearer(await login(client, 8100))

    resp = await client.post("/v1/channels/suggest", json={"ref": "@already"}, headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "subscribed"
    assert body["channel"]["id"] == channel.id

    mine = (await client.get("/v1/library/channels", headers=auth)).json()
    assert [c["id"] for c in mine] == [channel.id]
    assert await session.scalar(select(ChannelCandidate.id)) is None


async def test_a_user_asking_for_an_unknown_channel_is_queued_and_counted(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth_a = bearer(await login(client, 8101))
    auth_b = bearer(await login(client, 8102))

    first = await client.post("/v1/channels/suggest", json={"ref": "@wanted"}, headers=auth_a)
    assert first.json()["status"] == "queued"
    second = await client.post("/v1/channels/suggest", json={"ref": "t.me/wanted"}, headers=auth_b)
    assert second.json()["candidate_id"] == first.json()["candidate_id"]

    candidate = (await session.scalars(select(ChannelCandidate))).one()
    assert candidate.source == "user"
    assert len(candidate.requested_by_user_ids) == 2
    assert candidate.score >= 10  # two people want it


async def test_seeding_creates_channels_the_crawler_will_pick_up(
    session: AsyncSession,
) -> None:
    await make_channel(session, "known_one", status="active")
    result = await discovery.import_usernames(
        session,
        """
        username
        @first_chan
        https://t.me/second_chan
        t.me/s/third_chan
        known_one
        first_chan
        oops!
        """,
    )
    assert (result.created, result.existing) == (3, 1)
    assert result.invalid == ["oops!"]

    rows = list(await session.scalars(select(Channel).where(Channel.source_type == "web_preview")))
    created = {c.username for c in rows}
    assert {"first_chan", "second_chan", "third_chan"} <= created
    fresh = next(c for c in rows if c.username == "first_chan")
    assert (fresh.status, fresh.crawl_status) == ("pending", "idle")
    assert fresh.next_crawl_at is not None
    # Seeds are recorded as approved candidates, so the panel shows where they came from.
    seeded = await session.scalar(
        select(ChannelCandidate.status).where(ChannelCandidate.username == "first_chan")
    )
    assert seeded == "approved"


# ── review ────────────────────────────────────────────────────────────────────


async def test_approving_a_candidate_indexes_it_and_gives_it_to_everyone_who_asked(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 8200))
    user_id = int((await client.get("/v1/me", headers=auth)).json()["id"])
    await client.post("/v1/channels/suggest", json={"ref": "@requested"}, headers=auth)
    candidate_id = int(await session.scalar(select(ChannelCandidate.id)) or 0)

    headers = await admin_client(client, session)
    resp = await client.post(f"/admin/candidates/{candidate_id}/approve", headers=headers)
    assert resp.status_code == 200, resp.text
    channel_id = resp.json()["channel_id"]

    session.expire_all()
    channel = (await session.scalars(select(Channel).where(Channel.id == channel_id))).one()
    assert (channel.username, channel.source_type, channel.status) == (
        "requested",
        "web_preview",
        "pending",
    )
    # Attached to the asker's library immediately — no per-user indexing job exists.
    link = await session.scalar(
        select(UserChannel.channel_id).where(
            UserChannel.user_id == user_id, UserChannel.channel_id == channel_id
        )
    )
    assert link == channel_id
    refreshed = (
        await session.scalars(select(ChannelCandidate).where(ChannelCandidate.id == candidate_id))
    ).one()
    assert refreshed.status == "approved"
    assert refreshed.reviewed_at is not None


async def test_rejecting_blacklists_the_username_for_good(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    source = await make_channel(session, "src")
    await discovery.record_mentions(session, ["spam_chan", "other_chan"], discovered_from=source.id)
    await session.commit()
    ids = list(await session.scalars(select(ChannelCandidate.id)))

    headers = await admin_client(client, session)
    resp = await client.post(
        "/admin/candidates/reject",
        json={"ids": ids, "reason": "not music"},
        headers=headers,
    )
    assert resp.json() == {"rejected": 2}

    session.expire_all()
    assert await session.scalar(select(Blacklist.value).where(Blacklist.value == "spam_chan"))
    # Discovery cannot suggest it again …
    assert await discovery.submit(session, "spam_chan", source="crawl_mention") is None
    # … a user cannot ask for it …
    with pytest.raises(Forbidden):
        await discovery.suggest(session, 1, "@spam_chan")
    # … and a seed import refuses it too.
    result = await discovery.import_usernames(session, "spam_chan")
    assert (result.created, result.blocked) == (0, 1)


async def test_the_queue_is_ordered_by_score(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    for name, score in (("meh", 5.0), ("great", 90.0), ("fine", 40.0)):
        session.add(ChannelCandidate(username=name, source="crawl_mention", score=score))
    await session.commit()

    headers = await admin_client(client, session)
    page = (await client.get("/admin/candidates?limit=2", headers=headers)).json()
    assert [row["username"] for row in page["items"]] == ["great", "fine"]
    assert page["total"] == 3


async def test_candidate_endpoints_need_permission(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    assert (await client.get("/admin/candidates")).status_code == 401
    assert (await client.post("/admin/candidates/1/approve")).status_code == 401


# ── probing (what fills the score in) ─────────────────────────────────────────


async def test_a_probe_turns_one_page_into_a_score(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    source = await make_channel(session, "src")
    await discovery.record_mentions(session, ["probe_me"], discovered_from=source.id)
    await session.commit()

    claimed = await discovery.claim_probes(session, 5)
    assert [c.username for c in claimed] == ["probe_me"]
    assert claimed[0].probe_attempts == 1
    # A probe may be retried once (a page fetch can simply fail) and then never again.
    assert [c.username for c in await discovery.claim_probes(session, 5)] == ["probe_me"]
    assert await discovery.claim_probes(session, 5) == []

    candidate = await discovery.apply_probe(
        session,
        claimed[0].id,
        title="Probe Me",
        subscribers=12_000,
        messages=20,
        audio=18,
        newest_msg_id=4_000,
        posts_per_day=6.5,
    )
    assert candidate is not None
    assert candidate.audio_ratio == pytest.approx(0.9)
    assert candidate.tracks_estimate == 3_600
    assert candidate.subscribers == 12_000
    assert candidate.score > 50
    assert candidate.probed_at is not None


async def test_a_candidate_without_a_preview_is_rejected_by_the_probe(
    session: AsyncSession,
) -> None:
    source = await make_channel(session, "src")
    await discovery.record_mentions(session, ["private_one"], discovered_from=source.id)
    candidate = (await session.scalars(select(ChannelCandidate))).one()

    probed = await discovery.apply_probe(
        session,
        candidate.id,
        title=None,
        subscribers=None,
        messages=0,
        audio=0,
        newest_msg_id=None,
        posts_per_day=None,
        unavailable=True,
    )
    assert probed is not None
    assert (probed.status, probed.reject_reason) == ("rejected", "preview_disabled")
    assert probed.score == 0


async def test_the_probe_queue_is_served_over_the_internal_api(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The edge claims candidates through the same internal API as crawl tasks."""
    source = await make_channel(session, "src")
    await discovery.record_mentions(session, ["over_http"], discovered_from=source.id)
    await session.execute(
        text("UPDATE feature_flags SET value = '\"crawler\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    from app.services import plans

    plans.clear_caches()

    auth = {"Authorization": "Bearer internal-token"}
    claim = await client.post(
        "/internal/indexer/crawl/candidates/claim",
        json={"worker_id": "edge-1", "limit": 5},
        headers=auth,
    )
    (task,) = claim.json()["tasks"]
    assert task["username"] == "over_http"

    stats = await client.post(
        f"/internal/indexer/crawl/candidates/{task['candidate_id']}/stats",
        json={
            "title": "Over HTTP",
            "messages": 20,
            "audio": 20,
            "newest_msg_id": 900,
            "posts_per_day": 4.0,
        },
        headers=auth,
    )
    assert stats.json()["score"] > 0

    await session.execute(
        text("UPDATE feature_flags SET value = '\"mtproto\"' WHERE key = 'indexing_source'")
    )
    await session.commit()
    plans.clear_caches()


# ── edges the queue has to survive ────────────────────────────────────────────


async def test_approving_a_channel_that_failed_before_puts_it_back_in_the_queue(
    session: AsyncSession,
) -> None:
    """A channel that was private last month may be public today."""
    await make_channel(
        session,
        "second_chance",
        status="failed",
        status_reason="ChannelPrivateError",
        crawl_status="error",
        fail_count=5,
    )
    channel = await discovery.ensure_channel(session, "second_chance")

    assert (channel.status, channel.status_reason) == ("pending", None)
    assert (channel.crawl_status, channel.fail_count) == ("idle", 0)
    assert channel.next_crawl_at is not None


async def test_approving_an_unknown_candidate_is_a_clean_404(session: AsyncSession) -> None:
    from app.errors import NotFound

    with pytest.raises(NotFound):
        await discovery.approve(session, 10**9)


async def test_rejecting_nothing_does_nothing(session: AsyncSession) -> None:
    assert await discovery.reject(session, []) == 0


async def test_a_blacklisted_candidate_cannot_be_approved(session: AsyncSession) -> None:
    source = await make_channel(session, "srcx")
    await discovery.record_mentions(session, ["bad_chan"], discovered_from=source.id)
    candidate = (await session.scalars(select(ChannelCandidate))).one()
    candidate_id = candidate.id
    await discovery.reject(session, [candidate_id], reason="spam")

    with pytest.raises(Forbidden):
        await discovery.approve(session, candidate_id)
    assert await session.scalar(select(Channel.id).where(Channel.username == "bad_chan")) is None


async def test_a_probe_for_a_candidate_that_is_gone_is_not_an_error(
    session: AsyncSession,
) -> None:
    assert (
        await discovery.apply_probe(
            session,
            10**9,
            title=None,
            subscribers=None,
            messages=0,
            audio=0,
            newest_msg_id=None,
            posts_per_day=None,
        )
        is None
    )


async def test_suggesting_a_private_link_is_refused_with_a_reason(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    auth = bearer(await login(client, 8300))
    resp = await client.post(
        "/v1/channels/suggest", json={"ref": "https://t.me/+AbCdEfGh"}, headers=auth
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["reason"] == "private_link"


async def test_an_import_stops_at_the_batch_ceiling(session: AsyncSession) -> None:
    """A pasted file with ten thousand lines must not become ten thousand channels."""
    raw = "\n".join(f"chan_{i:05d}" for i in range(discovery.MAX_IMPORT + 50))
    result = await discovery.import_usernames(session, raw)
    assert result.created == discovery.MAX_IMPORT
