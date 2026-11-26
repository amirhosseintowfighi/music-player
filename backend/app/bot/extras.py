"""Bot commands for a quick moment in the chat: /random, /daily, /stats, /quiz."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import t
from app.config import Settings
from app.errors import AppError
from app.models import User
from app.services import achievements, daily_song, library, listening_stats, stream


async def _me(session: AsyncSession, tg: TgUser | None) -> User | None:
    if tg is None:
        return None
    from app.bot.handlers import _user  # the handlers module registers this one

    return await _user(session, tg)


def _open(settings: Settings, path: str, label: str) -> InlineKeyboardButton:
    url = f"{settings.webapp_url.split('#', 1)[0].rstrip('/')}/#/{path}"
    return InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))


async def send_random(
    message: Message, session: AsyncSession, settings: Settings, user: User
) -> None:
    """A song from the listener's own library: the audio itself when we have it, else a button."""
    lang = user.lang
    track_id = await daily_song.pick(session, user.id)
    found = (
        await library.hydrate_tracks(session, [track_id], "en" if lang == "en" else "fa")
        if track_id
        else []
    )
    if not found:
        await message.answer(t("random_none", lang))
        return
    track = found[0]
    artist = "، ".join(a.name for a in track.artists) or "—"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [_open(settings, f"track/{track.id}", t("play_song", lang))],
            [InlineKeyboardButton(text=t("random_another", lang), callback_data="random:again")],
        ]
    )
    text = t("random_song", lang, title=track.title, artist=artist)
    try:
        source = await stream.pick_source(
            session, track.id, settings.bot_id, settings.bot_api_max_download
        )
    except AppError:
        source = None
    if source is not None and source.bot_file_id:
        # Our own bot has this file: send the song itself, it plays right in the chat.
        await message.answer_audio(source.bot_file_id, caption=text, reply_markup=keyboard)
        return
    await message.answer(text, reply_markup=keyboard)


async def on_random(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await _me(session, message.from_user)
    if user is not None:
        await send_random(message, session, settings, user)


async def on_random_again(query: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await query.answer()
    user = await _me(session, query.from_user)
    if user is not None and isinstance(query.message, Message):
        await send_random(query.message, session, settings, user)


async def on_daily(message: Message, command: CommandObject, session: AsyncSession) -> None:
    """/daily turns the song of the day on; /daily off turns it off."""
    user = await _me(session, message.from_user)
    if user is None:
        return
    off = (command.args or "").strip().lower() in {"off", "stop", "خاموش", "0", "no"}
    await daily_song.set_enabled(session, user.id, not off)
    await message.answer(t("daily_off" if off else "daily_on", user.lang))


async def on_stats(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await _me(session, message.from_user)
    if user is None:
        return
    lang = user.lang
    week = await listening_stats.for_user(session, user.id, 7, lang=lang)
    streak = await achievements.streak_for(session, user.id)
    top = ""
    if week.top_track_ids:
        found = await library.hydrate_tracks(
            session, [week.top_track_ids[0][0]], "en" if lang == "en" else "fa"
        )
        if found:
            top = t("stats_top", lang, title=found[0].title)
    await message.answer(
        t(
            "stats_text",
            lang,
            minutes=week.minutes,
            plays=week.plays,
            songs=week.songs,
            streak=streak.current,
            longest=streak.longest,
            top=top,
        ).rstrip(),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_open(settings, "stats", t("stats_open", lang))]]
        ),
    )


async def on_quiz(message: Message, session: AsyncSession, settings: Settings) -> None:
    user = await _me(session, message.from_user)
    if user is None:
        return
    await message.answer(
        t("quiz_intro", user.lang),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_open(settings, "quiz", t("quiz_open", user.lang))]]
        ),
    )


def register(router: Router) -> None:
    router.message.register(on_random, Command("random"))
    router.message.register(on_daily, Command("daily"))
    router.message.register(on_stats, Command("stats"))
    router.message.register(on_quiz, Command("quiz"))
    router.callback_query.register(on_random_again, F.data == "random:again")
