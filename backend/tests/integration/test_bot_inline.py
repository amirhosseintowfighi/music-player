"""Inline mode: ``@bot <song>`` answers with audio where it can and a card where not."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from aiogram.methods import AnswerInlineQuery
from aiogram.types import (
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultCachedAudio,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import EdgeNode, Track, User
from app.services import stream
from app.services.ingest import ingest_items
from tests.integration.helpers import item, make_channel
from tests.integration.test_bot import USER, RecordingSession, send, tg  # noqa: F401
from tmusic_common.stream_ticket import verify

_n = iter(range(50_000, 60_000))


def inline_query(query: str, offset: str = "") -> dict[str, Any]:
    return {
        "update_id": next(_n),
        "inline_query": {
            "id": str(next(_n)),
            "from": USER,
            "query": query,
            "offset": offset,
        },
    }


def answer(tg: RecordingSession) -> AnswerInlineQuery:  # noqa: F811
    calls = [c for c in tg.calls if isinstance(c, AnswerInlineQuery)]
    assert calls, [type(c).__name__ for c in tg.calls]
    return calls[-1]


@pytest.fixture
async def catalog(session: AsyncSession, settings: Settings) -> dict[str, int]:
    stream.clear_edge_cache()
    channel = await make_channel(session, "inlinetunes")
    await ingest_items(
        session,
        channel,
        [
            item("Googoosh - Pol", fuid="AgADinl1"),
            item("Googoosh - Talagh", fuid="AgADinl2"),
            item("Googoosh - Hejrat", fuid="AgADinl3", file_size=40_000_000),
        ],
    )
    session.add(EdgeNode(host="cdn.example.test"))
    await session.commit()
    ids = {
        fuid: tid
        for fuid, tid in (
            await session.execute(
                select(Track.file_unique_id, Track.id).where(Track.file_unique_id.like("AgADinl%"))
            )
        ).tuples()
    }
    # One song our bot has seen (a file id), one resolved MP3, one too big for a URL.
    await session.execute(
        update(Track)
        .where(Track.id == ids["AgADinl1"])
        .values(bot_file_id="CQAC-inline", bot_id=settings.bot_id)
    )
    await session.execute(update(Track).values(resolve_status="resolved"))
    await session.commit()
    yield ids
    stream.clear_edge_cache()


async def test_search_sends_the_audio_or_a_card(
    client: httpx.AsyncClient,
    tg: RecordingSession,  # noqa: F811
    catalog: dict[str, int],
    settings: Settings,
    session: AsyncSession,
) -> None:
    await send(client, inline_query("googoosh"))
    sent = answer(tg)
    by_id = {r.id: r for r in sent.results}
    assert set(by_id) == {f"t{tid}" for tid in catalog.values()}
    assert sent.is_personal is True

    cached = by_id[f"t{catalog['AgADinl1']}"]
    assert isinstance(cached, InlineQueryResultCachedAudio)
    assert cached.audio_file_id == "CQAC-inline"

    by_url = by_id[f"t{catalog['AgADinl2']}"]
    assert isinstance(by_url, InlineQueryResultAudio)
    assert by_url.audio_url.startswith("https://cdn.example.test/s/")
    ticket = verify(by_url.audio_url.split("t=", 1)[1], settings.signing_keys)
    assert ticket.track_id == catalog["AgADinl2"]
    assert by_url.performer == "گوگوش"  # the Persian name for a Persian-speaking user

    # 40 MB is past what Telegram fetches by URL: a card that opens the Mini App.
    card = by_id[f"t{catalog['AgADinl3']}"]
    assert isinstance(card, InlineQueryResultArticle)
    button = card.reply_markup.inline_keyboard[0][0]  # type: ignore[union-attr]
    assert button.url == f"https://t.me/{settings.bot_username}?startapp=tr_{catalog['AgADinl3']}"

    # The person asking became a user, so the deep link lands them in the app.
    assert await session.scalar(select(User.id).where(User.tg_id == USER["id"])) is not None


async def test_an_empty_query_suggests_something(
    client: httpx.AsyncClient,
    tg: RecordingSession,  # noqa: F811
    catalog: dict[str, int],
) -> None:
    await send(client, inline_query(""))
    sent = answer(tg)
    assert sent.results  # trending falls back to the catalogue before any snapshot
    assert sent.next_offset == ""
    assert sent.button is not None and sent.button.web_app is not None


async def test_without_an_edge_everything_but_cached_audio_is_a_card(
    client: httpx.AsyncClient,
    tg: RecordingSession,  # noqa: F811
    catalog: dict[str, int],
    session: AsyncSession,
) -> None:
    await session.execute(update(EdgeNode).values(healthy=False))
    await session.commit()
    stream.clear_edge_cache()
    await send(client, inline_query("googoosh"))
    kinds = {type(r) for r in answer(tg).results}
    assert kinds == {InlineQueryResultCachedAudio, InlineQueryResultArticle}


async def test_banned_users_get_nothing(
    client: httpx.AsyncClient,
    tg: RecordingSession,  # noqa: F811
    catalog: dict[str, int],
    session: AsyncSession,
) -> None:
    await send(client, inline_query("x"))
    await session.execute(update(User).where(User.tg_id == USER["id"]).values(is_banned=True))
    await session.commit()
    await send(client, inline_query("googoosh"))
    assert answer(tg).results == []
