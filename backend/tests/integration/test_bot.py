"""Bot handlers, driven through the real webhook endpoint with a recording Bot API session."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers import mtproto_channel_id
from app.bot.texts import TEXTS, t
from app.models import Channel, Track, User, UserChannel

HOOK = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}
CHANNEL_CHAT_ID = -1001234567890


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.calls.append(method)
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass

    def texts(self) -> list[str]:
        return [getattr(c, "text", "") for c in self.calls if hasattr(c, "text")]


@pytest.fixture
def tg(client: httpx.AsyncClient) -> RecordingSession:
    session = RecordingSession()
    client.app.state.bot.session = session  # type: ignore[attr-defined]
    return session


_update_id = iter(range(1, 10_000))
USER = {"id": 9001, "is_bot": False, "first_name": "Sara", "language_code": "fa"}


def private_message(**fields: Any) -> dict[str, Any]:
    return {
        "update_id": next(_update_id),
        "message": {
            "message_id": next(_update_id),
            "date": int(time.time()),
            "chat": {"id": USER["id"], "type": "private"},
            "from": USER,
            **fields,
        },
    }


async def send(client: httpx.AsyncClient, update: dict[str, Any]) -> None:
    resp = await client.post("/tg/webhook", json=update, headers=HOOK)
    assert resp.status_code == 200, resp.text


def test_channel_id_conversion() -> None:
    assert mtproto_channel_id(CHANNEL_CHAT_ID) == 1234567890


def test_all_texts_have_both_languages() -> None:
    for key, table in TEXTS.items():
        assert set(table) == {"fa", "en"}, key
    assert t("open_player", "en") == TEXTS["open_player"]["en"]
    assert t("limit_channels", "fa", limit=3).count("3") == 1


async def test_webhook_rejects_bad_secret(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/tg/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "x"}
    )
    assert resp.status_code == 401
    assert (await client.post("/tg/webhook", json={"update_id": 1})).status_code == 401


async def test_start_registers_user(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    await send(
        client,
        private_message(
            text="/start pl_abc", entities=[{"type": "bot_command", "offset": 0, "length": 6}]
        ),
    )
    user = (await session.scalars(select(User).where(User.tg_id == 9001))).one()
    assert user.first_name == "Sara"
    assert tg.texts()[0].startswith("سلام Sara")
    markup = tg.calls[0].reply_markup  # type: ignore[attr-defined]
    assert markup.inline_keyboard[0][0].web_app.url == "https://app.example.test"


async def test_help_and_language(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    await send(
        client,
        private_message(text="/help", entities=[{"type": "bot_command", "offset": 0, "length": 5}]),
    )
    await send(
        client,
        private_message(text="/lang", entities=[{"type": "bot_command", "offset": 0, "length": 5}]),
    )
    assert tg.texts()[0] == TEXTS["help"]["fa"]
    await send(
        client,
        {
            "update_id": next(_update_id),
            "callback_query": {"id": "cb1", "from": USER, "chat_instance": "1", "data": "lang:en"},
        },
    )
    user = (await session.scalars(select(User).where(User.tg_id == 9001))).one()
    assert user.lang == "en"


async def test_add_channel_by_text(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    await send(client, private_message(text="https://t.me/SomeMusic"))
    channel = (await session.scalars(select(Channel).where(Channel.username == "somemusic"))).one()
    assert channel.status == "pending"
    assert "SomeMusic" in tg.texts()[-1] or "somemusic" in tg.texts()[-1].lower()

    await send(client, private_message(text="https://t.me/+private"))
    assert tg.texts()[-1] == TEXTS["private_channel"]["fa"]
    await send(client, private_message(text="hello there"))
    assert tg.texts()[-1] == TEXTS["not_a_channel"]["fa"]


async def test_channel_limit_message(client: httpx.AsyncClient, tg: RecordingSession) -> None:
    for name in ("limitone", "limittwo"):
        await send(client, private_message(text=f"@{name}"))
    assert tg.texts()[-1] == t("limit_channels", "fa", limit=1)


async def test_add_channel_by_forward(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    origin = {
        "type": "channel",
        "chat": {
            "id": CHANNEL_CHAT_ID,
            "type": "channel",
            "title": "Fwd Music",
            "username": "fwdmusic",
        },
        "message_id": 10,
        "date": int(time.time()),
    }
    await send(client, private_message(text="song", forward_origin=origin))
    channel = (await session.scalars(select(Channel).where(Channel.username == "fwdmusic"))).one()
    assert channel.tg_channel_id == 1234567890
    assert channel.title == "Fwd Music"

    private_origin = {**origin, "chat": {"id": -1009999, "type": "channel", "title": "Secret"}}
    await send(client, private_message(text="x", forward_origin=private_origin))
    assert tg.texts()[-1] == TEXTS["private_channel"]["fa"]
    user_origin = {"type": "user", "sender_user": USER, "date": int(time.time())}
    await send(client, private_message(text="x", forward_origin=user_origin))
    assert tg.texts()[-1] == TEXTS["not_a_channel"]["fa"]


def admin_member(user: dict[str, Any]) -> dict[str, Any]:
    """Every required permission flag of the installed aiogram version, set to False."""
    from aiogram.types import ChatMemberAdministrator

    flags = {
        name: False
        for name, field in ChatMemberAdministrator.model_fields.items()
        if field.is_required() and name not in ("status", "user")
    }
    return {"status": "administrator", "user": user, **flags, "can_post_messages": True}


def member_update(status: str) -> dict[str, Any]:
    bot_user = {"id": 123456, "is_bot": True, "first_name": "Bot"}
    return {
        "update_id": next(_update_id),
        "my_chat_member": {
            "chat": {"id": CHANNEL_CHAT_ID, "type": "channel", "title": "Private Tunes"},
            "from": USER,
            "date": int(time.time()),
            "old_chat_member": {"status": "left", "user": bot_user},
            "new_chat_member": (
                admin_member(bot_user)
                if status == "administrator"
                else {"status": status, "user": bot_user}
            ),
        },
    }  # fmt: skip


def channel_audio(message_id: int, file_unique_id: str) -> dict[str, Any]:
    return {
        "update_id": next(_update_id),
        "channel_post": {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": {"id": CHANNEL_CHAT_ID, "type": "channel", "title": "Private Tunes"},
            "audio": {
                "file_id": f"CQAC-{file_unique_id}",
                "file_unique_id": file_unique_id,
                "duration": 210,
                "performer": "Mahasti",
                "title": "Soghati",
                "file_size": 3_000_000,
                "mime_type": "audio/mpeg",
            },
            "caption": "@privatetunes",
        },
    }


async def test_bot_admin_channel_flow(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    await send(
        client,
        private_message(
            text="/start", entities=[{"type": "bot_command", "offset": 0, "length": 6}]
        ),
    )
    await send(client, member_update("administrator"))
    channel = (
        await session.scalars(select(Channel).where(Channel.tg_channel_id == 1234567890))
    ).one()
    assert (channel.source, channel.status, channel.is_public) == ("bot_admin", "active", False)
    user = (await session.scalars(select(User).where(User.tg_id == 9001))).one()
    sub = await session.scalar(select(UserChannel.channel_id).where(UserChannel.user_id == user.id))
    assert sub == channel.id
    assert tg.texts()[-1] == t("admin_connected", "fa", title="Private Tunes")

    await send(client, channel_audio(5, "AgADbotaudio"))
    track = (
        await session.scalars(select(Track).where(Track.file_unique_id == "AgADbotaudio"))
    ).one()
    assert (track.bot_file_id, track.bot_id, track.title) == (
        "CQAC-AgADbotaudio",
        123456,
        "Soghati",
    )

    await send(client, member_update("left"))
    session.expire_all()
    channel = (
        await session.scalars(select(Channel).where(Channel.tg_channel_id == 1234567890))
    ).one()
    assert (channel.status, channel.status_reason) == ("paused", "bot_removed")
    await send(client, channel_audio(6, "AgADignored"))
    assert (
        await session.scalar(select(Track.id).where(Track.file_unique_id == "AgADignored")) is None
    )

    await send(client, member_update("administrator"))
    session.expire_all()
    channel = (
        await session.scalars(select(Channel).where(Channel.tg_channel_id == 1234567890))
    ).one()
    assert channel.status == "active"


async def test_audio_from_unknown_channel_creates_it(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    await send(client, channel_audio(1, "AgADorphan"))
    channel = (
        await session.scalars(select(Channel).where(Channel.tg_channel_id == 1234567890))
    ).one()
    assert channel.source == "bot_admin"
    assert (
        await session.scalar(select(Track.id).where(Track.file_unique_id == "AgADorphan"))
        is not None
    )


async def test_invalid_update_is_acknowledged(
    client: httpx.AsyncClient, tg: RecordingSession
) -> None:
    await send(client, {"update_id": 5, "message": {"message_id": "not-an-int"}})


async def test_failing_handler_still_acks(
    client: httpx.AsyncClient, tg: RecordingSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.bot import handlers

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(handlers.users, "upsert_telegram_user", boom)
    await send(
        client,
        private_message(text="/help", entities=[{"type": "bot_command", "offset": 0, "length": 5}]),
    )


# ── music people send to the bot ──────────────────────────────────────────────


def audio_field(file_unique_id: str, title: str = "Gole Sangam") -> dict[str, Any]:
    return {
        "file_id": f"CQAC-{file_unique_id}",
        "file_unique_id": file_unique_id,
        "duration": 245,
        "performer": "Hayedeh",
        "title": title,
        "file_size": 4_000_000,
        "mime_type": "audio/mpeg",
    }


async def test_a_track_sent_to_the_bot_is_indexed_and_playable_at_once(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    """No MTProto anywhere: the bot received the file, so it has a Bot API file id."""
    await send(client, private_message(audio=audio_field("AgADupload01")))

    track = (
        await session.scalars(select(Track).where(Track.file_unique_id == "AgADupload01"))
    ).one()
    assert track.bot_file_id == "CQAC-AgADupload01"
    assert track.resolve_status == "resolved"
    assert track.playable
    assert "Gole Sangam" in " ".join(tg.texts())


async def test_the_same_file_from_two_people_is_one_track(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    """Uploads share one channel, so their entries are keyed by the file itself."""
    await send(client, private_message(audio=audio_field("AgADupload02")))
    await send(client, private_message(audio=audio_field("AgADupload02")))

    tracks = (
        await session.scalars(select(Track).where(Track.file_unique_id == "AgADupload02"))
    ).all()
    assert len(tracks) == 1
    assert any("قبل" in text or "already" in text for text in tg.texts())


async def test_a_forwarded_channel_post_adds_the_channel_and_the_track(
    client: httpx.AsyncClient, tg: RecordingSession, session: AsyncSession
) -> None:
    """One forward does both jobs: discovers the channel, and banks the file id."""
    await send(
        client,
        private_message(
            audio=audio_field("AgADupload03", title="Pol"),
            forward_origin={
                "type": "channel",
                "date": int(time.time()),
                "message_id": 771,
                "chat": {
                    "id": CHANNEL_CHAT_ID,
                    "type": "channel",
                    "title": "Golden Tunes",
                    "username": "goldentunes",
                },
            },
        ),
    )

    channel = (
        await session.scalars(select(Channel).where(Channel.username == "goldentunes"))
    ).one()
    track = (
        await session.scalars(select(Track).where(Track.file_unique_id == "AgADupload03"))
    ).one()
    assert track.bot_file_id == "CQAC-AgADupload03"
    assert track.channels_count >= 1
    assert channel.title == "Golden Tunes"
