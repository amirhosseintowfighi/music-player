"""Channels with no public web preview, handed to a logged-in account.

The flag is the whole point: off, nothing changes and those channels stay parked;
on, they are handed over — including the ones that already gave up before anyone
turned it on, which is the case every existing deployment is in.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import crawling, plans

from .helpers import make_channel


async def set_flag(session: AsyncSession, on: bool) -> None:
    await session.execute(
        text(
            "UPDATE feature_flags SET value = CAST(:v AS jsonb) WHERE key = 'mtproto_fallback'"
        ).bindparams(v="true" if on else "false")
    )
    await session.commit()
    plans.clear_caches()


@pytest.fixture(autouse=True)
async def fallback_off(session: AsyncSession) -> Any:
    await set_flag(session, False)
    yield
    await set_flag(session, False)


async def claim_one(session: AsyncSession) -> list[crawling.CrawlTask]:
    tasks = await crawling.claim(session, "w1", limit=10)
    await session.commit()
    return tasks


async def test_the_flag_is_off_by_default(session: AsyncSession) -> None:
    """It carries a ban risk the web path does not, so nobody gets it by accident."""
    assert await plans.get_flag(session, "mtproto_fallback", None) is False


async def test_a_preview_less_channel_is_parked_while_the_flag_is_off(
    session: AsyncSession,
) -> None:
    channel = await make_channel(session, "noweb", status="indexing")
    tasks = await claim_one(session)
    token = next(t.lease_token for t in tasks if t.channel_id == channel.id)

    assert await crawling.report_failure(
        session, channel.id, token, reason="preview_disabled", preview_disabled=True
    )
    await session.commit()
    await session.refresh(channel)

    assert channel.crawl_status == "preview_disabled"
    assert channel.source_type == "web_preview"
    assert channel.id not in [t.channel_id for t in await claim_one(session)]


async def test_with_the_flag_on_the_same_failure_hands_it_to_the_account(
    session: AsyncSession,
) -> None:
    channel = await make_channel(session, "handover", status="indexing")
    tasks = await claim_one(session)
    token = next(t.lease_token for t in tasks if t.channel_id == channel.id)
    await set_flag(session, True)

    await crawling.report_failure(
        session, channel.id, token, reason="preview_disabled", preview_disabled=True
    )
    await session.commit()
    await session.refresh(channel)

    assert channel.source_type == "mtproto"
    assert channel.crawl_status == "idle"
    assert channel.fail_count == 0  # the web attempt is not held against the account

    task = next(t for t in await claim_one(session) if t.channel_id == channel.id)
    assert task.source == "mtproto"


async def test_turning_the_flag_on_un_parks_channels_that_already_gave_up(
    session: AsyncSession,
) -> None:
    """Every existing deployment is in this state, so it cannot need manual repair."""
    channel = await make_channel(
        session,
        "parked",
        status="indexing",
        crawl_status="preview_disabled",
        preview_available=False,
        fail_count=1,
    )
    await session.commit()

    assert channel.id not in [t.channel_id for t in await claim_one(session)]

    await set_flag(session, True)
    task = next((t for t in await claim_one(session) if t.channel_id == channel.id), None)
    assert task is not None
    assert task.source == "mtproto"
    await session.refresh(channel)
    assert channel.source_type == "mtproto"


async def test_web_channels_keep_their_own_source(session: AsyncSession) -> None:
    await set_flag(session, True)
    channel = await make_channel(session, "normalweb", status="indexing")
    await session.commit()

    task = next(t for t in await claim_one(session) if t.channel_id == channel.id)
    assert task.source == "web_preview"
