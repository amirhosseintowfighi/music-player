"""/random, /daily, /stats and /quiz, and the song of the day."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from aiogram.methods import SendAudio, SendMessage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, User
from app.services import daily_song, notifications
from app.services.ingest import ingest_items
from tests.integration.helpers import item, make_channel, subscribe
from tests.integration.test_bot import USER, RecordingSession, private_message, send, tg

__all__ = ["tg"]


def command(name: str) -> dict[str, Any]:
    return private_message(
        text=f"/{name}", entities=[{"type": "bot_command", "offset": 0, "length": len(name) + 1}]
    )


async def me(session: AsyncSession) -> User:
    user = (await session.scalars(select(User).where(User.tg_id == USER["id"]))).one()
    await session.refresh(user)
    return user


async def test_random_with_nothing_and_with_a_library(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    await send(client, command("random"))
    assert "هنوز آهنگی نداری" in tg.texts()[-1]

    channel = await make_channel(session, "randchan")
    await ingest_items(session, channel, [item("Ebi - Khaneh", msg=1)], bot_id=None)
    await subscribe(session, (await me(session)).id, channel.id)
    await session.commit()
    await send(client, command("random"))
    sent = [c for c in tg.calls if isinstance(c, (SendMessage, SendAudio))][-1]
    body = sent.text if isinstance(sent, SendMessage) else sent.caption
    assert body is not None and "Khaneh" in body
    buttons = sent.reply_markup.inline_keyboard  # type: ignore[union-attr]
    assert buttons[0][0].web_app is not None and "#/track/" in buttons[0][0].web_app.url
    assert buttons[1][0].callback_data == "random:again"


async def test_daily_on_and_off(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    await send(client, command("daily"))
    assert "هر روز" in tg.texts()[-1]
    assert (await me(session)).notification_prefs["daily_song"] is True
    off = private_message(
        text="/daily off", entities=[{"type": "bot_command", "offset": 0, "length": 6}]
    )
    await send(client, off)
    assert (await me(session)).notification_prefs["daily_song"] is False


async def test_stats_and_quiz(client: httpx.AsyncClient, tg: RecordingSession) -> None:
    await send(client, command("stats"))
    assert "۰" in tg.texts()[-1] or "0" in tg.texts()[-1]
    assert "🔥" in tg.texts()[-1]
    await send(client, command("quiz"))
    last = [c for c in tg.calls if isinstance(c, SendMessage)][-1]
    assert last.reply_markup.inline_keyboard[0][0].web_app.url.endswith("#/quiz")  # type: ignore[union-attr]


async def test_the_song_of_the_day_goes_to_those_who_asked_at_their_eleven(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    await send(client, command("start"))
    user = await me(session)
    channel = await make_channel(session, "dailychan")
    await ingest_items(session, channel, [item("Googoosh - Pol", msg=1)])
    await subscribe(session, user.id, channel.id)
    await session.commit()

    # 07:30 UTC = 11:00 in Tehran. Not asked for yet: nothing.
    eleven = datetime(2026, 11, 25, 7, 30, tzinfo=UTC)
    assert await daily_song.queue_due(session, eleven) == 0
    await daily_song.set_enabled(session, user.id, True)
    await session.commit()
    assert await daily_song.queue_due(session, datetime(2026, 11, 25, 5, 0, tzinfo=UTC)) == 0
    assert await daily_song.queue_due(session, eleven) == 1
    assert await daily_song.queue_due(session, eleven) == 0  # once a day
    await session.commit()
    notice = (await session.scalars(select(Notification))).one()
    assert notice.kind == "daily_song"
    assert notice.payload["open"].startswith("track/")
    assert (
        notifications.render("daily_song", notice.payload, "en") == "🎁 Your song for today: «Pol»"
    )
    assert await session.scalar(text("SELECT count(*) FROM notifications")) == 1
