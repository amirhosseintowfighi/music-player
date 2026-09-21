"""The job wrappers themselves: they are what production actually calls.

The services underneath have their own tests; what these check is the wiring —
that a job commits its work, reads its settings, and reports honest counters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db import make_sessionmaker
from app.services import resolving, social
from app.services.ingest import ingest_items
from app.workers import jobs
from tests.integration.helpers import item, make_channel
from tests.integration.test_social import make_user, play


@pytest.fixture
def ctx(engine: AsyncEngine) -> dict[str, Any]:
    return {"sessionmaker": make_sessionmaker(engine)}


async def test_rebuild_wrapped_stores_a_report_per_listener(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    channel = await make_channel(session, "wrapch")
    await ingest_items(session, channel, [item("آهنگ", "معین", msg=9700)], bot_id=None)
    track_id = (await session.execute(text("SELECT id FROM tracks LIMIT 1"))).scalar_one()

    listener = await make_user(session, 84001)
    silent = await make_user(session, 84002)
    listener_id, silent_id = listener.id, silent.id
    await play(session, listener_id, track_id, year=datetime.now(UTC).year)
    await session.commit()

    result = await jobs.rebuild_wrapped(ctx)
    assert result["users"] == 1  # the silent user has nothing to wrap

    session.expire_all()
    rows = await session.execute(text("SELECT user_id FROM wrapped_reports"))
    assert [row[0] for row in rows] == [listener_id]
    # The stored report is what the endpoint reads back.
    assert (await social.wrapped(session, listener_id))["plays"] == 1
    assert (await social.wrapped(session, silent_id))["plays"] == 0


async def test_probe_metadata_is_a_no_op_without_an_edge(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    """No edge configured means no probing — not a crash, and not a silent retry loop."""
    assert await jobs.probe_metadata(ctx) == {"probed": 0, "filled": 0, "failed": 0}


async def test_probe_metadata_runs_through_the_edge(
    session: AsyncSession, ctx: dict[str, Any], engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = await make_channel(session, "probech")
    await ingest_items(
        session,
        channel,
        [item("ترک", "گوگوش", msg=9710, file_size=4_000_000)],
        bot_id=None,
    )
    track_id = (await session.execute(text("SELECT id FROM tracks LIMIT 1"))).scalar_one()
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"track_id": track_id, "album": "از تگ", "year": 2004, "genre": "Pop"}
        )

    from app.config import get_settings

    configured = get_settings().model_copy(update={"edge_internal_url": "http://edge.test"})
    monkeypatch.setattr(jobs, "get_settings", lambda: configured)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await jobs.probe_metadata({**ctx, "http": http})

    assert result == {"probed": 1, "filled": 1, "failed": 0}
    session.expire_all()
    row = await session.execute(
        text("SELECT album, year, genre FROM tracks WHERE id = :id").bindparams(id=track_id)
    )
    assert row.one() == ("از تگ", 2004, "Pop")


async def test_maintenance_daily_keeps_partitions_and_prunes(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    await session.execute(
        text(
            "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)"
            " SELECT id, gen_random_uuid(), decode(md5(random()::text), 'hex'),"
            " now() - interval '30 days' FROM users LIMIT 1"
        )
    )
    user = await make_user(session, 84010)
    await session.execute(
        text(
            "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at)"
            " VALUES (:u, gen_random_uuid(), decode(md5('x'), 'hex'), now() - interval '30 days')"
        ).bindparams(u=user.id)
    )
    await session.commit()

    await jobs.maintenance_daily(ctx)

    session.expire_all()
    left = await session.execute(text("SELECT count(*) FROM refresh_tokens"))
    assert left.scalar_one() == 0  # long-expired tokens are gone
    partitions = await session.execute(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relname LIKE 'play_history_%'"
        )
    )
    assert partitions.scalar_one() >= 2  # future months exist before anyone writes to them


# ── the crawler's daily health check (ADR-002 §2, layer A) ────────────────────


async def add_crawl_day(session: AsyncSession, days_ago: int, messages: int, audio: int) -> None:
    await session.execute(
        text(
            "INSERT INTO crawl_stats_daily (day, pages, messages, audio_items, empty_pages)"
            " VALUES (current_date - CAST(:d AS int), 10, :m, :a, 0)"
            " ON CONFLICT (day) DO UPDATE SET messages = excluded.messages,"
            " audio_items = excluded.audio_items"
        ).bindparams(d=days_ago, m=messages, a=audio)
    )


class FakeBot:
    """Stands in for aiogram: records who was told what."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.session = SimpleNamespace(close=self._close)

    async def send_message(self, tg_id: int, body: str) -> None:
        self.sent.append((tg_id, body))

    async def _close(self) -> None:
        return None


async def test_crawler_healthcheck_is_quiet_when_everything_is_fine(
    session: AsyncSession, ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for day in range(1, 6):
        await add_crawl_day(session, day, messages=200, audio=180)
    await add_crawl_day(session, 0, messages=200, audio=175)
    await session.commit()

    bot = FakeBot()
    monkeypatch.setattr(jobs, "build_bot", lambda _settings: bot)
    assert await jobs.crawler_healthcheck(ctx) == {"alerts": 0, "notified": 0}
    assert bot.sent == []  # nobody is woken up for a normal day


async def test_crawler_healthcheck_tells_the_admins_the_parser_broke(
    session: AsyncSession, ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The silent failure: pages still parse, no tracks come out."""
    for day in range(1, 6):
        await add_crawl_day(session, day, messages=200, audio=180)
    await add_crawl_day(session, 0, messages=200, audio=3)
    await session.execute(
        text(
            "INSERT INTO admin_users (tg_id, role, permissions, is_active)"
            " VALUES (91001, 'owner', ARRAY['*'], true), (91002, 'admin', ARRAY['*'], false)"
        )
    )
    await session.commit()

    bot = FakeBot()
    monkeypatch.setattr(jobs, "build_bot", lambda _settings: bot)
    result = await jobs.crawler_healthcheck(ctx)

    assert result == {"alerts": 1, "notified": 1}  # only the active admin
    (tg_id, body) = bot.sent[0]
    assert tg_id == 91001
    assert "پارسر" in body
    assert "RUNBOOK" in body


async def test_crawler_healthcheck_reports_a_dead_resolver_and_failing_channels(
    session: AsyncSession, ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_channel(session, "brokenchan", source_type="web_preview", crawl_status="error")
    await session.execute(
        text(
            "INSERT INTO admin_users (tg_id, role, permissions, is_active)"
            " VALUES (91003, 'owner', ARRAY['*'], true)"
        )
    )
    await session.commit()
    for _ in range(resolving.BREAKER_THRESHOLD):
        resolving.breaker.trip()

    bot = FakeBot()
    monkeypatch.setattr(jobs, "build_bot", lambda _settings: bot)
    result = await jobs.crawler_healthcheck(ctx)
    resolving.reset_breaker()

    assert result["alerts"] == 2  # the errored channel and the open breaker
    body = bot.sent[0][1]
    assert "resolver" in body
    assert "کانال در وضعیت خطای کرال" in body


async def test_crawler_healthcheck_does_not_need_a_bot_when_nobody_is_an_admin(
    session: AsyncSession, ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_channel(session, "lonelychan", source_type="web_preview", crawl_status="error")
    await session.commit()

    def explode(_settings: Any) -> Any:  # a bot must not even be built
        raise AssertionError("no admins, no bot")

    monkeypatch.setattr(jobs, "build_bot", explode)
    assert await jobs.crawler_healthcheck(ctx) == {"alerts": 1, "notified": 0}
