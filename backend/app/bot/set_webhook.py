"""python -m app.bot.set_webhook — registers the webhook and bot commands."""

from __future__ import annotations

import asyncio

from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonWebApp, WebAppInfo

from app.bot.setup import ALLOWED_UPDATES, build_bot
from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.webhook_url:
        raise SystemExit("WEBHOOK_URL is not set")
    bot = build_bot(settings)
    try:
        await bot.set_webhook(
            settings.webhook_url,
            secret_token=settings.webhook_secret.get_secret_value(),
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=False,
            max_connections=100,
        )
        for lang, commands in {
            "fa": [("start", "شروع"), ("help", "راهنما"), ("lang", "تغییر زبان")],
            "en": [("start", "Start"), ("help", "Help"), ("lang", "Language")],
        }.items():
            await bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in commands],
                scope=BotCommandScopeDefault(),
                language_code=lang,
            )
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Player", web_app=WebAppInfo(url=settings.webapp_url))
        )
        print("webhook set:", settings.webhook_url)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
