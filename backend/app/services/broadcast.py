"""Broadcasts: segmentation, A/B variants, scheduling and a rate-limited send loop.

Telegram allows roughly 30 messages per second to different chats. Going faster earns
a 429 with a `retry_after`, and ignoring that gets the bot limited for everyone, so the
worker paces itself and always honours `retry_after` instead of retrying immediately.

Progress is written back to the ``broadcasts`` row as the run goes, which is what the
admin panel polls; a crashed worker therefore resumes from the counters, not from zero.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import metrics
from app.config import Settings
from app.db import session_scope
from app.errors import Conflict, InvalidInput, NotFound
from app.models import Broadcast
from app.security.adminauth import AdminClaims
from app.services import admin as admin_service
from tmusic_common.logging import get_logger

log = get_logger(__name__)

MESSAGES_PER_SECOND = 25  # under Telegram's ~30/s ceiling, with headroom
BATCH = 100
MAX_VARIANTS = 4


@dataclass(frozen=True, slots=True)
class Variant:
    text: str
    photo_file_id: str | None = None
    button_text: str | None = None
    button_url: str | None = None
    weight: int = 1


def parse_variants(raw: list[dict[str, Any]]) -> list[Variant]:
    if not raw or len(raw) > MAX_VARIANTS:
        raise InvalidInput("between 1 and 4 variants", count=len(raw))
    variants = []
    for entry in raw:
        body = str(entry.get("text", "")).strip()
        if not body or len(body) > 4000:
            raise InvalidInput("variant text must be 1..4000 characters")
        variants.append(
            Variant(
                text=body,
                photo_file_id=entry.get("photo_file_id"),
                button_text=entry.get("button_text"),
                button_url=entry.get("button_url"),
                weight=max(1, int(entry.get("weight", 1))),
            )
        )
    return variants


# ── segmentation ──────────────────────────────────────────────────────────────

# Every filter is a fixed fragment; the values are always bound parameters.
_SEGMENTS: dict[str, str] = {
    "plan": "u.plan_code = :plan",
    "lang": "u.lang = :lang",
    "active_days": "u.last_seen_at > now() - make_interval(days => :active_days)",
    "inactive_days": "u.last_seen_at < now() - make_interval(days => :inactive_days)",
    "has_channels": "EXISTS (SELECT 1 FROM user_channels uc WHERE uc.user_id = u.id)",
    "expiring_days": (
        "EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id"
        " AND s.status IN ('active','trialing')"
        " AND s.expires_at < now() + make_interval(days => :expiring_days))"
    ),
    "never_paid": "NOT EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.id AND p.status='paid')",
}


def build_segment(target: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Turns the admin's filter object into a WHERE clause over ``users u``."""
    clauses = ["NOT u.is_banned", "NOT u.bot_blocked"]
    params: dict[str, Any] = {}
    for key, value in target.items():
        if key not in _SEGMENTS:
            raise InvalidInput("unknown segment filter", filter=key)
        if value is False or value is None:
            continue
        clauses.append(_SEGMENTS[key])
        if value is not True:
            params[key] = value
    return " AND ".join(clauses), params


async def estimate(session: AsyncSession, target: dict[str, Any]) -> int:
    where, params = build_segment(target)
    total = await session.scalar(
        text(f"SELECT count(*) FROM users u WHERE {where}").bindparams(**params)
    )
    return int(total or 0)


# ── lifecycle ─────────────────────────────────────────────────────────────────


async def create(
    session: AsyncSession,
    claims: AdminClaims,
    *,
    target: dict[str, Any],
    variants: list[dict[str, Any]],
    scheduled_at: datetime | None = None,
) -> Broadcast:
    admin_service.require(claims, "broadcast.send")
    parse_variants(variants)  # validates
    total = await estimate(session, target)
    broadcast = Broadcast(
        admin_id=claims.admin_id,
        target_filter=target,
        variants=variants,
        status="scheduled" if scheduled_at else "draft",
        scheduled_at=scheduled_at,
        total=total,
    )
    session.add(broadcast)
    await session.flush()
    await admin_service.audit(
        session,
        claims,
        "broadcast.create",
        "broadcast",
        broadcast.id,
        {"total": total, "variants": len(variants)},
    )
    return broadcast


async def set_status(
    session: AsyncSession, claims: AdminClaims, broadcast_id: int, status: str
) -> Broadcast:
    admin_service.require(claims, "broadcast.send")
    allowed = {"draft", "scheduled", "running", "paused", "canceled"}
    if status not in allowed:
        raise InvalidInput("unknown status", status=status)
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise NotFound("broadcast not found")
    if broadcast.status == "completed":
        raise Conflict("broadcast already finished")
    broadcast.status = status
    await admin_service.audit(
        session, claims, "broadcast.status", "broadcast", broadcast_id, {"status": status}
    )
    return broadcast


async def progress(session: AsyncSession, broadcast_id: int) -> dict[str, Any]:
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise NotFound("broadcast not found")
    done = broadcast.sent + broadcast.failed + broadcast.blocked
    return {
        "id": broadcast.id,
        "status": broadcast.status,
        "total": broadcast.total,
        "sent": broadcast.sent,
        "failed": broadcast.failed,
        "blocked": broadcast.blocked,
        "percent": round(100.0 * done / broadcast.total, 1) if broadcast.total else 0.0,
        "started_at": broadcast.started_at,
        "finished_at": broadcast.finished_at,
    }


async def due_broadcasts(session: AsyncSession) -> list[int]:
    rows = await session.execute(
        text(
            # A scheduled broadcast with no time is "send it now" — that is what the
            # panel's start button does. `NULL <= now()` is NULL, so it needs the
            # explicit IS NULL or such a broadcast would never leave the queue.
            "SELECT id FROM broadcasts WHERE status = 'running'"
            " OR (status = 'scheduled' AND (scheduled_at IS NULL OR scheduled_at <= now()))"
            " ORDER BY id"
        )
    )
    return [row[0] for row in rows]


# ── sending ───────────────────────────────────────────────────────────────────


def pick_variant(variants: list[Variant], user_id: int) -> Variant:
    """Deterministic per user, so a re-run sends the same variant to the same person."""
    total = sum(variant.weight for variant in variants)
    bucket = (user_id * 2654435761) % total  # Knuth hash, stable across runs
    for variant in variants:
        if bucket < variant.weight:
            return variant
        bucket -= variant.weight
    return variants[-1]


def keyboard(variant: Variant, settings: Settings) -> InlineKeyboardMarkup | None:
    if not variant.button_text:
        return None
    url = variant.button_url or settings.webapp_url
    button = (
        InlineKeyboardButton(text=variant.button_text, web_app=WebAppInfo(url=url))
        if url == settings.webapp_url
        else InlineKeyboardButton(text=variant.button_text, url=url)
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


async def _recipients(
    session: AsyncSession, broadcast: Broadcast, after_id: int, limit: int
) -> list[tuple[int, int]]:
    where, params = build_segment(broadcast.target_filter)
    rows = await session.execute(
        text(
            f"SELECT u.id, u.tg_id FROM users u WHERE {where} AND u.id > :after"
            " ORDER BY u.id LIMIT :limit"
        ).bindparams(after=after_id, limit=limit, **params)
    )
    return [(row[0], row[1]) for row in rows]


async def run(
    maker: async_sessionmaker[AsyncSession], bot: Bot, settings: Settings, broadcast_id: int
) -> dict[str, int]:
    """Sends one broadcast, pacing itself and persisting progress as it goes.

    Returns the final counters. Safe to call again after a crash: it walks user ids in
    order and skips everyone already counted.
    """
    async with session_scope(maker) as session:
        broadcast = await session.get(Broadcast, broadcast_id)
        if broadcast is None:
            raise NotFound("broadcast not found")
        if broadcast.status not in ("scheduled", "running"):
            return {"sent": broadcast.sent, "failed": broadcast.failed}
        broadcast.status = "running"
        if broadcast.started_at is None:
            broadcast.started_at = datetime.now(UTC)
        variants = parse_variants(broadcast.variants)
        cursor = broadcast.sent + broadcast.failed + broadcast.blocked

    sent = failed = blocked = 0
    after_id = 0
    delay = 1.0 / MESSAGES_PER_SECOND
    skip = cursor

    while True:
        async with session_scope(maker) as session:
            current = await session.get(Broadcast, broadcast_id)
            if current is None or current.status in ("paused", "canceled"):
                log.info(
                    "broadcast.stopped", id=broadcast_id, status=getattr(current, "status", "")
                )
                return {"sent": sent, "failed": failed, "blocked": blocked}
            batch = await _recipients(session, current, after_id, BATCH)
        if not batch:
            break

        for user_id, tg_id in batch:
            after_id = user_id
            if skip > 0:
                skip -= 1
                continue
            variant = pick_variant(variants, user_id)
            outcome = await _send_one(bot, tg_id, variant, settings)
            metrics.BROADCAST_MESSAGES.labels(outcome).inc()
            if outcome == "sent":
                sent += 1
            elif outcome == "blocked":
                blocked += 1
                await _mark_blocked(maker, user_id)
            else:
                failed += 1
            # A little jitter so a restarted worker does not sync up with another.
            await asyncio.sleep(delay + secrets.randbelow(1000) / 1000 * delay / 2)

        async with session_scope(maker) as session:
            await session.execute(
                text(
                    "UPDATE broadcasts SET sent = :sent, failed = :failed, blocked = :blocked"
                    " WHERE id = :id"
                ).bindparams(id=broadcast_id, sent=sent + cursor, failed=failed, blocked=blocked)
            )

    async with session_scope(maker) as session:
        await session.execute(
            text(
                "UPDATE broadcasts SET status = 'completed', finished_at = now(),"
                " sent = :sent, failed = :failed, blocked = :blocked WHERE id = :id"
            ).bindparams(id=broadcast_id, sent=sent + cursor, failed=failed, blocked=blocked)
        )
    log.info("broadcast.done", id=broadcast_id, sent=sent, failed=failed, blocked=blocked)
    return {"sent": sent, "failed": failed, "blocked": blocked}


async def _send_one(bot: Bot, tg_id: int, variant: Variant, settings: Settings) -> str:
    markup = keyboard(variant, settings)
    try:
        if variant.photo_file_id:
            await bot.send_photo(
                tg_id, variant.photo_file_id, caption=variant.text[:1024], reply_markup=markup
            )
        else:
            await bot.send_message(tg_id, variant.text, reply_markup=markup)
        return "sent"
    except TelegramForbiddenError:
        return "blocked"
    except TelegramRetryAfter as exc:
        # Telegram told us exactly how long to wait; never retry sooner.
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.send_message(tg_id, variant.text, reply_markup=markup)
            return "sent"
        except TelegramAPIError:
            return "failed"
    except TelegramAPIError:
        return "failed"


async def _mark_blocked(maker: async_sessionmaker[AsyncSession], user_id: int) -> None:
    async with session_scope(maker) as session:
        await session.execute(
            text("UPDATE users SET bot_blocked = true WHERE id = :id").bindparams(id=user_id)
        )


async def list_broadcasts(session: AsyncSession, limit: int = 50) -> list[Broadcast]:
    rows = await session.scalars(select(Broadcast).order_by(Broadcast.id.desc()).limit(limit))
    return list(rows.all())
