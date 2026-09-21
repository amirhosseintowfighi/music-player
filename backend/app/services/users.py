from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.security.initdata import TelegramUser


def effective_plan(user: User, now: datetime | None = None) -> str:
    """A paid plan only counts while ``premium_until`` is in the future."""
    if user.plan_code == "free":
        return "free"
    if user.premium_until is None or user.premium_until <= (now or datetime.now(UTC)):
        return "free"
    return user.plan_code


def ui_lang(language_code: str | None) -> str:
    return "fa" if (language_code or "fa").lower().startswith("fa") else "en"


async def upsert_telegram_user(session: AsyncSession, tg: TelegramUser) -> User:
    """Create or refresh a user from Telegram data. UI language is only set on creation."""
    stmt = (
        insert(User)
        .values(
            tg_id=tg.id,
            username=tg.username,
            first_name=tg.first_name[:128],
            last_name=(tg.last_name or None) and tg.last_name[:128],
            tg_is_premium=tg.is_premium,
            lang=ui_lang(tg.language_code),
            referral_code=secrets.token_urlsafe(6),
        )
        .on_conflict_do_update(
            index_elements=[User.tg_id],
            set_={
                "username": tg.username,
                "first_name": tg.first_name[:128],
                "last_name": (tg.last_name or None) and tg.last_name[:128],
                "tg_is_premium": tg.is_premium,
                "last_seen_at": func.now(),
            },
        )
        .returning(User)
    )
    user = (await session.scalars(stmt, execution_options={"populate_existing": True})).one()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    return (await session.scalars(select(User).where(User.tg_id == tg_id))).one_or_none()
