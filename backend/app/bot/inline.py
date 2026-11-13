"""Inline mode: ``@bot <song>`` in any chat, and the song itself goes into that chat.

What a result sends depends on what we hold for the track:

* a Bot API file id of our own bot — the audio is sent as is, instantly;
* a resolved MP3 within the Bot API's 20 MB URL limit — Telegram fetches it from the
  edge with a signed, short-lived ticket and posts it as audio;
* neither — a card with the title and a button that opens it in the Mini App.

Inline results never count against the daily play limit: sending a song to a friend
is not listening to it, and every result brings someone new to the bot.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InlineQueryResultCachedAudio,
    InlineQueryResultsButton,
    InlineQueryResultUnion,
    InputTextMessageContent,
    WebAppInfo,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import t
from app.config import Settings
from app.errors import AppError
from app.schemas import TrackOut
from app.security.initdata import TelegramUser
from app.services import history, library, recommendations, search, stream, users
from app.services.library import Lang, TrackFilters
from app.services.meili import MeiliClient
from tmusic_common.logging import get_logger
from tmusic_common.stream_ticket import sign

log = get_logger(__name__)

PAGE = 20
MAX_OFFSET = 200
# Telegram downloads a URL-sent audio within seconds; an hour covers retries.
URL_TICKET_TTL_S = 3600
URL_MAX_BYTES = 20 * 1024 * 1024
CACHE_S = 30
MP3 = ("audio/mpeg", "audio/mp3")


def _artists(track: TrackOut) -> str:
    return "، ".join(a.name for a in track.artists if a.role == "primary") or "—"


def app_link(settings: Settings, track_id: int) -> str:
    return f"https://t.me/{settings.bot_username}?startapp=tr_{track_id}"


def _open_keyboard(settings: Settings, lang: str, track_id: int) -> InlineKeyboardMarkup:
    # A web_app button is not allowed in an inline message; a startapp link is.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("inline_open", lang), url=app_link(settings, track_id))]
        ]
    )


async def _result(
    session: AsyncSession,
    settings: Settings,
    user_id: int,
    lang: str,
    track: TrackOut,
    thumb: str | None,
    edge: str | None,
) -> InlineQueryResultUnion:
    rid = f"t{track.id}"
    performer = _artists(track)
    keyboard = _open_keyboard(settings, lang, track.id)
    caption = t("inline_caption", lang, bot=settings.bot_username)
    try:
        source = await stream.pick_source(
            session, track.id, settings.bot_id, settings.bot_api_max_download
        )
    except AppError:
        source = None
    if source is not None and source.bot_file_id:
        return InlineQueryResultCachedAudio(
            id=rid, audio_file_id=source.bot_file_id, caption=caption, reply_markup=keyboard
        )
    if (
        source is not None
        and edge is not None
        and source.resolved
        and (source.mime or "").lower() in MP3
        and 0 < source.file_size <= URL_MAX_BYTES
    ):
        ticket = stream.build_ticket(source, user_id, URL_TICKET_TTL_S)
        token = sign(ticket, settings.signing_keys[0])
        return InlineQueryResultAudio(
            id=rid,
            audio_url=f"{stream.edge_base_url(edge)}/s/{source.track_id}?t={token}",
            title=track.title or "—",
            performer=performer,
            audio_duration=track.duration or None,
            caption=caption,
            reply_markup=keyboard,
        )
    return InlineQueryResultArticle(
        id=rid,
        title=track.title or "—",
        description=performer,
        thumbnail_url=thumb,
        input_message_content=InputTextMessageContent(
            message_text=t("inline_card", lang, title=track.title or "—", artist=performer)
        ),
        reply_markup=keyboard,
    )


async def on_inline_query(
    query: InlineQuery,
    session: AsyncSession,
    settings: Settings,
    redis: Redis,
    meili: MeiliClient | None = None,
) -> None:
    tg = query.from_user
    user = await users.upsert_telegram_user(
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
    lang: Lang = "en" if user.lang == "en" else "fa"
    button = InlineQueryResultsButton(
        text=t("inline_button", lang), web_app=WebAppInfo(url=settings.webapp_url)
    )
    if user.is_banned:
        await query.answer([], cache_time=CACHE_S, is_personal=True)
        return
    try:
        offset = max(0, min(MAX_OFFSET, int(query.offset or 0)))
    except ValueError:
        offset = 0
    text = query.query.strip()
    if text and meili is not None:
        hit = await search.search_tracks(
            session, meili, text, channel_ids=None, filters=TrackFilters(),
            limit=PAGE, offset=offset,
        )  # fmt: skip
        ids, more = hit.ids, offset + len(hit.ids) < min(hit.total, MAX_OFFSET)
    elif not text:
        # Nothing typed yet: what they played last, then what everyone is playing.
        recent = await history.recent_ids(session, user.id, PAGE)
        ids = list(dict.fromkeys(recent + await recommendations.trending_ids(session, limit=PAGE)))
        ids, more = ids[:PAGE], False
    else:
        ids, more = [], False
    tracks = [tr for tr in await library.hydrate_tracks(session, ids, lang) if tr.playable]
    try:
        edge: str | None = await stream.pick_edge(session)
        thumbs = await stream.thumbnail_urls(
            session, settings, user.id, [tr.id for tr in tracks if tr.has_thumb]
        )
    except AppError:
        # No edge right now: cached audio still works, the rest become cards.
        edge, thumbs = None, {}
    results = [
        await _result(session, settings, user.id, lang, tr, thumbs.get(str(tr.id)), edge)
        for tr in tracks
    ]
    await query.answer(
        results,
        cache_time=CACHE_S,
        is_personal=True,
        next_offset=str(offset + PAGE) if more else "",
        button=button,
    )
    log.info("bot.inline", results=len(results), offset=offset, empty=not text)
