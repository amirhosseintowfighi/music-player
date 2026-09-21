from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    connect_args: dict[str, object] = {"server_settings": {"application_name": "tmusic"}}
    if settings.db_behind_pgbouncer:
        # PgBouncer in transaction mode cannot keep server-side prepared statements.
        connect_args |= {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        }
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on error.

    A context manager rather than a bare generator on purpose: with ``async for`` a
    caller that ``return``s from inside the loop closes the generator without resuming
    it, the commit never runs, and the work is silently rolled back.
    """
    async with maker() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        else:
            await session.commit()
