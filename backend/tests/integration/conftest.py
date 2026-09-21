from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.services import plans, stream
from tests.conftest import REFERENCE_TABLES


@pytest.fixture(autouse=True)
async def _clean_db(
    engine: AsyncEngine, seeded_artist_max: int, seeded_settings: dict[str, list[tuple[str, str]]]
) -> AsyncIterator[None]:
    artist_max = seeded_artist_max
    async with engine.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') "
                    "AND NOT c.relispartition AND c.relname NOT IN ('alembic_version', 'artists')"
                )
            )
        ).scalars()
        to_clear = [t for t in tables if t not in REFERENCE_TABLES]
        await conn.execute(text(f"TRUNCATE {', '.join(to_clear)} RESTART IDENTITY CASCADE"))
        await conn.execute(text("DELETE FROM artists WHERE id > :m").bindparams(m=artist_max))
        await conn.execute(
            text("UPDATE artists SET tracks_count = 0, merged_into_id = NULL, hidden = false")
        )
        for table, rows_to_restore in seeded_settings.items():
            for key, value in rows_to_restore:
                await conn.execute(
                    text(
                        f"INSERT INTO {table} (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                        "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
                    ).bindparams(k=key, v=value)
                )
    plans.clear_caches()
    stream.clear_edge_cache()
    yield


@pytest.fixture
def paid_settings(settings: Settings) -> Settings:
    """Settings with gateway credentials, as production would have them in env."""
    return settings.model_copy(
        update={
            "zarinpal_merchant_id": SecretStr("merchant-1"),
            "idpay_api_key": SecretStr("idpay-key"),
            "nextpay_api_key": SecretStr("nextpay-key"),
            "public_api_url": "https://api.example.test",
        }
    )
