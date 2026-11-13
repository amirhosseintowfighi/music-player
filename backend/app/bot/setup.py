from __future__ import annotations

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import DbSessionMiddleware, build_router
from app.config import Settings
from app.services.meili import MeiliClient

ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "my_chat_member",
    "channel_post",
    "pre_checkout_query",
    "inline_query",
]


def build_bot(settings: Settings) -> Bot:
    # Bot API traffic leaves through the edge egress proxy (ADR-0002).
    session = AiohttpSession(api=TelegramAPIServer.from_base(settings.tg_api_base))
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )


def build_dispatcher(
    settings: Settings,
    maker: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    redis: Redis,
    meili: MeiliClient | None = None,
) -> Dispatcher:
    dp = Dispatcher(settings=settings, http=http, redis=redis, meili=meili)
    middleware = DbSessionMiddleware(maker)
    for observer in (
        dp.message,
        dp.callback_query,
        dp.my_chat_member,
        dp.channel_post,
        dp.pre_checkout_query,
        dp.inline_query,
    ):
        observer.middleware(middleware)
    dp.include_router(build_router())
    return dp
