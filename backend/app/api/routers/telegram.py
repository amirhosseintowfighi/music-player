"""Telegram webhook. Updates arrive via the edge (hook.<domain>) over the tunnel."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, FastAPI, Header, Request
from pydantic import ValidationError

from app.api.deps import State
from app.api.state import AppState
from app.bot.setup import build_bot, build_dispatcher
from app.errors import NotFound, Unauthorized
from tmusic_common.logging import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["telegram"], include_in_schema=False)


async def startup(app: FastAPI) -> None:
    state: AppState = app.state.app
    app.state.bot = build_bot(state.settings)
    app.state.dp = build_dispatcher(state.settings, state.sessionmaker, state.http)


async def shutdown(app: FastAPI) -> None:
    bot: Bot | None = getattr(app.state, "bot", None)
    if bot is not None:
        await bot.session.close()


@router.post("/tg/webhook")
async def webhook(
    request: Request,
    state: State,
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> dict[str, bool]:
    expected = state.settings.webhook_secret.get_secret_value()
    if not secret or not hmac.compare_digest(secret, expected):
        raise Unauthorized("bad webhook secret")
    bot: Bot | None = getattr(request.app.state, "bot", None)
    dp: Dispatcher | None = getattr(request.app.state, "dp", None)
    if bot is None or dp is None:
        raise NotFound("bot disabled")
    payload: dict[str, Any] = await request.json()
    try:
        update = Update.model_validate(payload, context={"bot": bot})
    except ValidationError:
        # Acknowledge anyway: a non-2xx answer makes Telegram redeliver it forever.
        log.warning("bot.invalid_update", update_id=payload.get("update_id"), exc_info=True)
        return {"ok": True}
    try:
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("bot.update_failed", update_id=update.update_id)
    return {"ok": True}
