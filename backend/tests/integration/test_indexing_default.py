"""The shipped defaults must index something.

A freshly migrated database used to say ``indexing_source = "mtproto"`` — the pool
that phase 6 deleted. Nothing crawled, nothing failed, and a channel added on a new
installation stayed in ``indexing`` until someone went looking in feature_flags.
Nothing in the suite noticed, because every crawler test turns the flag on first.

So this test turns nothing on.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import crawling, plans


async def test_a_fresh_install_crawls_without_touching_any_flag(session: AsyncSession) -> None:
    plans.clear_caches()
    assert await crawling.enabled(session) is True


async def test_the_dead_lazy_resolve_flag_is_gone(session: AsyncSession) -> None:
    row = await session.execute(
        text("SELECT count(*) FROM feature_flags WHERE key = 'lazy_resolve'")
    )
    assert row.scalar_one() == 0
