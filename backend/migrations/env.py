import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    # Migrations bypass PgBouncer: MIGRATION_DATABASE_URL points straight at Postgres.
    settings = get_settings()
    engine = create_async_engine(settings.migration_database_url or settings.database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
asyncio.run(_main())
