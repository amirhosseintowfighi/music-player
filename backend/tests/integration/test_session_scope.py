"""``session_scope`` must commit even when the caller returns early.

This is a regression test for a real bug: while ``session_scope`` was a bare async
generator, ``async for session in session_scope(...): return x`` closed the generator
without resuming it, so the commit never ran and every job that returned from inside
the loop silently discarded its writes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db import make_sessionmaker, session_scope


async def _insert_and_return_early(engine: AsyncEngine) -> int:
    async with session_scope(make_sessionmaker(engine)) as session:
        row = await session.execute(
            text(
                "INSERT INTO users (tg_id, first_name, referral_code)"
                " VALUES (93001, 'Early', 'r93001') RETURNING id"
            )
        )
        return int(row.scalar_one())


async def test_an_early_return_still_commits(session: AsyncSession, engine: Any) -> None:
    user_id = await _insert_and_return_early(engine)

    found = await session.execute(
        text("SELECT count(*) FROM users WHERE id = :id").bindparams(id=user_id)
    )
    assert found.scalar_one() == 1


async def test_an_exception_still_rolls_back(session: AsyncSession, engine: Any) -> None:
    class Boom(Exception):
        pass

    try:
        async with session_scope(make_sessionmaker(engine)) as scoped:
            await scoped.execute(
                text(
                    "INSERT INTO users (tg_id, first_name, referral_code)"
                    " VALUES (93002, 'Doomed', 'r93002')"
                )
            )
            raise Boom
    except Boom:
        pass

    found = await session.execute(text("SELECT count(*) FROM users WHERE tg_id = 93002"))
    assert found.scalar_one() == 0
