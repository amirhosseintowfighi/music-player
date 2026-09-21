"""Test harness.

Runs against a real PostgreSQL 16 (TEST_DATABASE_URL) and a real Meilisearch
(TEST_MEILI_URL; search tests skip if it is unreachable). Redis is faked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import fakeredis
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.main import create_app
from app.api.routers import telegram
from app.api.state import AppState
from app.config import Settings
from app.db import make_engine, make_sessionmaker
from app.security.initdata import sign_init_data
from app.services.meili import MeiliClient

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:55432/tmusic_test"
)
MEILI_URL = os.environ.get("TEST_MEILI_URL", "http://127.0.0.1:7777")
MEILI_KEY = os.environ.get("TEST_MEILI_KEY", "test-master-key-0123456789abcdef")
BOT_TOKEN = "123456:TEST-token_for-tests"
REFERENCE_TABLES = {"plans", "channel_categories", "settings", "feature_flags", "payment_providers"}

_key = Ed25519PrivateKey.generate()
PRIVATE_PEM = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PUBLIC_PEM = (
    _key.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
ENV = {
    "ENV": "test",
    "DATABASE_URL": DB_URL,
    "DB_BEHIND_PGBOUNCER": "false",
    "BOT_TOKEN": BOT_TOKEN,
    "BOT_USERNAME": "tmusic_test_bot",
    "WEBAPP_URL": "https://app.example.test",
    "WEBHOOK_SECRET": "hook-secret",
    "JWT_PRIVATE_KEY": PRIVATE_PEM,
    "JWT_PUBLIC_KEY": PUBLIC_PEM,
    "STREAM_SIGNING_KEYS": "stream-key-1,stream-key-old",
    "INTERNAL_API_TOKEN": "internal-token",
    "MEILI_URL": MEILI_URL,
    "MEILI_API_KEY": MEILI_KEY,
    "PAYMENTS_ADMIN_CHAT_ID": "-1009999",
    "LOG_JSON": "false",
    "LOG_LEVEL": "WARNING",
}
os.environ.update(ENV)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(meili_index=f"tracks_test_{uuid.uuid4().hex[:8]}")  # type: ignore[call-arg]


async def _recreate_database() -> None:
    dsn = DB_URL.replace("+asyncpg", "")
    base, name = dsn.rsplit("/", 1)
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        await conn.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}'"
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    await _recreate_database()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        check=True,
        env={**os.environ, **ENV},
    )
    eng = make_engine(settings)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
async def seeded_settings(engine: AsyncEngine) -> dict[str, list[tuple[str, str]]]:
    """settings and feature_flags reference admin_users, so TRUNCATE CASCADE wipes them."""
    out: dict[str, list[tuple[str, str]]] = {}
    async with engine.connect() as conn:
        for table in ("settings", "feature_flags"):
            rows = await conn.execute(text(f"SELECT key, value::text FROM {table}"))
            out[table] = [(row[0], row[1]) for row in rows]
    return out


@pytest.fixture(scope="session")
async def seeded_artist_max(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT max(id) FROM artists"))).scalar_one())


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with make_sessionmaker(engine)() as s:
        yield s


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(scope="session")
async def meili_http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as http:
        yield http


@pytest.fixture(scope="session")
async def meili_available(meili_http: httpx.AsyncClient) -> bool:
    try:
        return (await meili_http.get(f"{MEILI_URL}/health", timeout=2)).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
async def meili(
    settings: Settings, meili_http: httpx.AsyncClient, meili_available: bool
) -> AsyncIterator[MeiliClient]:
    client = MeiliClient(meili_http, MEILI_URL, MEILI_KEY, settings.meili_index)
    if meili_available:
        await client.ensure_index()
        await client.wait(await client.delete_all())
    yield client


@pytest.fixture
def need_meili(meili_available: bool) -> None:
    if not meili_available:
        pytest.skip("Meilisearch not reachable at TEST_MEILI_URL")


@pytest.fixture
async def app_state(
    settings: Settings,
    engine: AsyncEngine,
    redis: fakeredis.aioredis.FakeRedis,
    meili: MeiliClient,
    meili_http: httpx.AsyncClient,
) -> AppState:
    return AppState(
        settings=settings,
        engine=engine,
        sessionmaker=make_sessionmaker(engine),
        redis=redis,
        http=meili_http,
        meili=meili,
    )


@pytest.fixture
async def client(app_state: AppState) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(app_state.settings, state=app_state)
    await telegram.startup(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c
    await telegram.shutdown(app)


def init_data_for(tg_id: int, first_name: str = "Test", **extra: str) -> str:
    user = {"id": tg_id, "first_name": first_name, "language_code": "fa"}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user), **extra}
    return sign_init_data(fields, BOT_TOKEN)


async def login(client: httpx.AsyncClient, tg_id: int = 1001) -> dict[str, Any]:
    resp = await client.post("/v1/auth/telegram", json={"init_data": init_data_for(tg_id)})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def bearer(token_body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_body['access_token']}"}


class QueryCounter:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)


@pytest.fixture
def count_queries(engine: AsyncEngine) -> Any:
    """``with count_queries() as q: ...; assert q.count <= N`` — guards against N+1."""

    @contextmanager
    def _counter() -> Iterator[QueryCounter]:
        counter = QueryCounter()

        def before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
            counter.statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", before)
        try:
            yield counter
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before)

    return _counter


def utcnow() -> datetime:
    return datetime.now(UTC)
