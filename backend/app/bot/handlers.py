from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginChannel,
    TelegramObject,
    WebAppInfo,
)
from aiogram.types import User as TgUser
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import payments as bot_payments
from app.bot.texts import t
from app.config import Settings
from app.errors import AppError, Forbidden, InvalidInput, LimitReached
from app.models import Channel, User
from app.security.initdata import TelegramUser
from app.services import channels, uploads, users
from app.services.channels import ChannelRef
from app.services.ingest import ingest_items
from tmusic_common.indexer_contract import AudioItem
from tmusic_common.logging import get_logger

log = get_logger(__name__)

_BOT_API_CHANNEL_OFFSET = 10**12


def mtproto_channel_id(bot_api_chat_id: int) -> int:
    """Bot API reports channels as -100XXXXXXXXXX; MTProto (and our DB) use XXXXXXXXXX."""
    return -bot_api_chat_id - _BOT_API_CHANNEL_OFFSET


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self.maker = maker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
            return result


async def _user(session: AsyncSession, tg: TgUser) -> User:
    return await users.upsert_telegram_user(
        session,
        TelegramUser(
            id=tg.id,
            first_name=tg.first_name,
            last_name=tg.last_name,
            username=tg.username,
            language_code=tg.language_code,
            is_premium=bool(tg.is_premium),
        ),
    )


def _player_keyboard(settings: Settings, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("open_player", lang), web_app=WebAppInfo(url=settings.webapp_url)
                )
            ]
        ]
    )


def _status_text(channel: Channel, lang: str) -> str:
    return t("status_ready" if channel.status == "active" else "status_pending", lang)


async def _add_and_reply(
    message: Message, session: AsyncSession, user: User, ref: ChannelRef, settings: Settings
) -> None:
    lang = user.lang
    try:
        channel, _ = await channels.subscribe(session, user.id, ref)
    except LimitReached as exc:
        await message.answer(t("limit_channels", lang, limit=exc.details.get("limit", 0)))
        return
    except Forbidden:
        await message.answer(t("blocked_channel", lang))
        return
    title = channel.title or (f"@{channel.username}" if channel.username else "—")
    await message.answer(
        t("channel_added", lang, title=title, status=_status_text(channel, lang)),
        reply_markup=_player_keyboard(settings, lang),
    )


async def on_start(
    message: Message, command: CommandObject, session: AsyncSession, settings: Settings
) -> None:
    if message.from_user is None:
        return
    user = await _user(session, message.from_user)
    if user.is_banned:
        await message.answer(t("banned", user.lang))
        return
    await message.answer(
        t("welcome", user.lang, name=message.from_user.first_name),
        reply_markup=_player_keyboard(settings, user.lang),
    )


async def on_help(message: Message, session: AsyncSession) -> None:
    if message.from_user:
        user = await _user(session, message.from_user)
        await message.answer(t("help", user.lang))


async def on_lang(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    user = await _user(session, message.from_user)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="فارسی", callback_data="lang:fa"),
                InlineKeyboardButton(text="English", callback_data="lang:en"),
            ]
        ]
    )
    await message.answer(t("choose_lang", user.lang), reply_markup=kb)


async def on_lang_chosen(query: CallbackQuery, session: AsyncSession) -> None:
    lang = (query.data or "lang:fa").split(":", 1)[1]
    await session.execute(update(User).where(User.tg_id == query.from_user.id).values(lang=lang))
    await query.answer(t("lang_set", lang))


def _item_of(message: Message, message_id: int) -> AudioItem | None:
    """The music in a message the bot received, with its Bot API file id.

    That file id is the point: a track that arrives this way is playable at once,
    with no MTProto account anywhere in the picture.
    """
    audio = message.audio
    if audio is None:
        return None
    return AudioItem(
        message_id=message_id,
        posted_at=message.date,
        file_unique_id=audio.file_unique_id,
        duration=audio.duration,
        file_size=audio.file_size or 0,
        mime_type=audio.mime_type,
        title=audio.title,
        performer=audio.performer,
        file_name=audio.file_name,
        caption=message.caption,
        has_thumb=audio.thumbnail is not None,
        bot_file_id=audio.file_id,
    )


async def on_forward(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return
    user = await _user(session, message.from_user)
    origin = message.forward_origin
    if not isinstance(origin, MessageOriginChannel):
        # Not from a channel, but it may still be music worth keeping.
        if message.audio is not None:
            await on_private_audio(message, session, settings)
            return
        await message.answer(t("not_a_channel", user.lang))
        return
    chat: Chat = origin.chat
    if not chat.username:
        await message.answer(t("private_channel", user.lang))
        return
    ref = ChannelRef(
        username=chat.username, tg_channel_id=mtproto_channel_id(chat.id), title=chat.title
    )
    await _add_and_reply(message, session, user, ref, settings)

    # The forwarded post itself is a track, and the bot just received the file — so
    # it is playable immediately, unlike the same track found by the crawler.
    item = _item_of(message, origin.message_id)
    channel = (
        await session.scalars(select(Channel).where(Channel.username == chat.username))
    ).one_or_none()
    if item is not None and channel is not None and channel.status != "blacklisted":
        await uploads.ingest_upload(session, channel, item, bot_id=settings.bot_id)


async def on_private_audio(message: Message, session: AsyncSession, settings: Settings) -> None:
    """A music file sent straight to the bot: index it and say what happened."""
    if message.from_user is None or message.audio is None:
        return
    user = await _user(session, message.from_user)
    item = _item_of(message, uploads.upload_message_id(message.audio.file_unique_id))
    if item is None:
        return
    channel = await uploads.uploads_channel(session)
    stats = await uploads.ingest_upload(session, channel, item, bot_id=settings.bot_id)
    key = "upload_added" if stats.inserted else "upload_known"
    title = item.title or item.file_name or "—"
    await message.answer(
        t(key, user.lang, title=title), reply_markup=_player_keyboard(settings, user.lang)
    )


async def on_text(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None or not message.text:
        return
    user = await _user(session, message.from_user)
    try:
        ref = channels.parse_channel_ref(message.text)
    except InvalidInput as exc:
        key = "private_channel" if exc.details.get("reason") == "private_link" else "not_a_channel"
        await message.answer(t(key, user.lang))
        return
    await _add_and_reply(message, session, user, ref, settings)


async def on_channel_membership(event: ChatMemberUpdated, session: AsyncSession, bot: Bot) -> None:
    """Path B: a user made the bot an admin of their channel."""
    chat = event.chat
    tg_id = mtproto_channel_id(chat.id)
    status = event.new_chat_member.status
    if status != ChatMemberStatus.ADMINISTRATOR:
        await session.execute(
            update(Channel)
            .where(Channel.tg_channel_id == tg_id, Channel.source == "bot_admin")
            .values(status="paused", status_reason="bot_removed")
        )
        return
    ref = ChannelRef(username=chat.username, tg_channel_id=tg_id, title=chat.title)
    user = await _user(session, event.from_user)
    try:
        channel, _ = await channels.subscribe(session, user.id, ref, source="bot_admin")
    except AppError as exc:
        log.info("bot.admin_channel_rejected", code=exc.code, channel=tg_id)
        return
    if channel.source == "bot_admin" and channel.status == "paused":
        channel.status, channel.status_reason = "active", None
    try:
        await bot.send_message(
            event.from_user.id, t("admin_connected", user.lang, title=chat.title or "")
        )
    except Exception:  # noqa: BLE001 — the user may never have started the bot
        log.info("bot.admin_notice_failed", user_id=event.from_user.id)


async def on_channel_audio(message: Message, session: AsyncSession, settings: Settings) -> None:
    """Every audio posted where the bot is admin; this also gives us Bot API file ids."""
    audio = message.audio
    if audio is None:
        return
    tg_id = mtproto_channel_id(message.chat.id)
    channel = (
        await session.scalars(select(Channel).where(Channel.tg_channel_id == tg_id))
    ).one_or_none()
    if channel is None:
        channel, _ = await channels.get_or_create_channel(
            session,
            ChannelRef(
                username=message.chat.username, tg_channel_id=tg_id, title=message.chat.title
            ),
            added_by=None,
            source="bot_admin",
        )
    if channel.status in ("blacklisted", "paused"):
        return
    item = AudioItem(
        message_id=message.message_id,
        posted_at=message.date,
        file_unique_id=audio.file_unique_id,
        duration=audio.duration,
        file_size=audio.file_size or 0,
        mime_type=audio.mime_type,
        title=audio.title,
        performer=audio.performer,
        file_name=audio.file_name,
        caption=message.caption,
        has_thumb=audio.thumbnail is not None,
        bot_file_id=audio.file_id,
    )
    stats = await ingest_items(session, channel, [item], bot_id=settings.bot_id)
    log.info("bot.channel_audio", channel_id=channel.id, inserted=stats.inserted)


def build_router() -> Router:
    """A fresh router per dispatcher (aiogram routers attach to one parent only)."""
    router = Router(name="main")
    bot_payments.register(router)
    router.message.register(on_start, CommandStart())
    router.message.register(on_help, Command("help"))
    router.message.register(on_lang, Command("lang"))
    router.callback_query.register(on_lang_chosen, F.data.in_({"lang:fa", "lang:en"}))
    router.message.register(on_forward, F.forward_origin)
    router.message.register(on_private_audio, F.chat.type == ChatType.PRIVATE, F.audio)
    router.message.register(on_text, F.chat.type == ChatType.PRIVATE, F.text)
    router.my_chat_member.register(on_channel_membership, F.chat.type == ChatType.CHANNEL)
    router.channel_post.register(on_channel_audio, F.audio)
    return router
