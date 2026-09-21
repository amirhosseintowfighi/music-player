"""Notifications: queue, preferences, quiet hours and a rate-limited sender.

A notification is only worth sending if the person wants it, it is not the middle of
their night, and we have not already told them the same thing. All three rules live
here rather than in each producer:

- **Preferences** are per kind, defaulting to on for the useful ones and off for the
  chatty ones. ``users.notification_prefs`` is a jsonb column because it is always read
  with the user row.
- **Quiet hours** are evaluated in the user's own offset (``tz_offset_minutes``,
  default Tehran). A notification created at 3am is not dropped — it is scheduled for
  the morning, because the news is still true then.
- **Dedup** uses the ``(user_id, dedup_key)`` unique index, so a producer that runs
  twice cannot message anyone twice.

Delivery is the same story as broadcasts: pace under Telegram's limit, honour
``retry_after``, and mark a user as blocked instead of retrying them forever.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import session_scope
from app.errors import InvalidInput
from tmusic_common.logging import get_logger

log = get_logger(__name__)

KINDS = ("new_tracks", "digest", "sub_expiry", "discover_ready", "payment", "system")
# Money and account news default on; discovery chatter defaults on but is easy to mute;
# the weekly digest is opt-out because it is the one people tire of first.
DEFAULTS: dict[str, bool] = {
    "new_tracks": True,
    "digest": True,
    "sub_expiry": True,
    "discover_ready": True,
    "payment": True,
    "system": True,
}
QUIET_START_HOUR = 23
QUIET_END_HOUR = 9
MESSAGES_PER_SECOND = 25
BATCH = 200


@dataclass(frozen=True, slots=True)
class Notice:
    user_id: int
    kind: str
    payload: dict[str, Any]
    dedup_key: str | None = None
    send_after: datetime | None = None


def wants(prefs: dict[str, Any], kind: str) -> bool:
    value = prefs.get(kind)
    return DEFAULTS.get(kind, True) if value is None else bool(value)


def next_allowed(now: datetime, tz_offset_minutes: int) -> datetime:
    """Moves a send time out of the user's quiet hours, never earlier than ``now``."""
    local = now + timedelta(minutes=tz_offset_minutes)
    hour = local.hour
    if QUIET_END_HOUR <= hour < QUIET_START_HOUR:
        return now
    # Before the morning boundary → this morning; after the evening one → tomorrow.
    target = local.replace(hour=QUIET_END_HOUR, minute=0, second=0, microsecond=0)
    if hour >= QUIET_START_HOUR:
        target += timedelta(days=1)
    return target - timedelta(minutes=tz_offset_minutes)


async def enqueue(session: AsyncSession, notices: list[Notice]) -> int:
    """Queues notices, skipping muted kinds and duplicates. Returns how many were queued."""
    if not notices:
        return 0
    unknown = {notice.kind for notice in notices} - set(KINDS)
    if unknown:
        raise InvalidInput("unknown notification kind", kinds=sorted(unknown))

    user_ids = sorted({notice.user_id for notice in notices})
    rows = await session.execute(
        text(
            "SELECT id, notification_prefs, tz_offset_minutes, bot_blocked, is_banned"
            " FROM users WHERE id = ANY(:ids)"
        ).bindparams(ids=user_ids)
    )
    people = {
        row.id: (
            row.notification_prefs or {},
            row.tz_offset_minutes,
            row.bot_blocked,
            row.is_banned,
        )
        for row in rows
    }

    now = datetime.now(UTC)
    values = []
    for notice in notices:
        found = people.get(notice.user_id)
        if found is None:
            continue
        prefs, offset, blocked, banned = found
        if blocked or banned or not wants(prefs, notice.kind):
            continue
        when = notice.send_after or now
        delayed = next_allowed(when, offset)
        values.append(
            {
                "user_id": notice.user_id,
                "kind": notice.kind,
                "payload": json.dumps(notice.payload, default=str),
                "dedup_key": notice.dedup_key,
                # NULL means "as soon as possible" and is filled in with the database
                # clock. Writing Python's clock here would land a few milliseconds in
                # the future of the transaction's own now(), and the row would sit in
                # the queue until the next tick for no reason. An explicit time, or one
                # pushed out of quiet hours, is of course kept.
                "send_after": (
                    delayed if notice.send_after is not None or delayed > when else None
                ),
            }
        )
    if not values:
        return 0

    # One statement per row rather than executemany: RETURNING is how we learn which
    # rows the dedup index rejected, and executemany discards it.
    queued = 0
    for row in values:
        result = await session.execute(
            text(
                "INSERT INTO notifications (user_id, kind, payload, dedup_key, send_after)"
                " VALUES (:user_id, :kind, CAST(:payload AS jsonb), :dedup_key,"
                " coalesce(:send_after, now()))"
                " ON CONFLICT (user_id, dedup_key) DO NOTHING RETURNING id"
            ).bindparams(**row)
        )
        if result.first() is not None:
            queued += 1
    log.info("notify.queued", requested=len(notices), queued=queued)
    return queued


async def claim_due(session: AsyncSession, limit: int = BATCH) -> list[dict[str, Any]]:
    """Takes a batch of due notifications, locking them so two workers cannot both send.

    ``SKIP LOCKED`` is what makes a second worker useful instead of a source of doubles.
    """
    rows = await session.execute(
        text(
            """
        WITH due AS (
            SELECT n.id FROM notifications n
             WHERE n.status = 'queued' AND n.send_after <= now()
             ORDER BY n.send_after
             LIMIT :limit FOR UPDATE SKIP LOCKED
        )
        UPDATE notifications n SET status = 'sent', sent_at = now()
          FROM due, users u
         WHERE n.id = due.id AND u.id = n.user_id
        RETURNING n.id, n.user_id, n.kind, n.payload, u.tg_id, u.lang
        """
        ).bindparams(limit=limit)
    )
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "kind": row.kind,
            "payload": row.payload,
            "tg_id": row.tg_id,
            "lang": row.lang,
        }
        for row in rows
    ]


def render(kind: str, payload: dict[str, Any], lang: str) -> str:
    """Notification copy. Kept here so producers pass data, not sentences."""
    from app.bot.texts import t

    if kind == "new_tracks":
        return t(
            "notify_new_tracks",
            lang,
            count=payload.get("count", 0),
            channel=payload.get("channel", ""),
        )
    if kind == "digest":
        return t(
            "notify_digest",
            lang,
            tracks=payload.get("tracks", 0),
            channels=payload.get("channels", 0),
        )
    if kind == "discover_ready":
        return t("notify_discover", lang)
    return str(payload.get("text", ""))


def keyboard(settings: Settings, lang: str) -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("open_player", lang), web_app=WebAppInfo(url=settings.webapp_url)
                )
            ]
        ]
    )


async def deliver(
    maker: async_sessionmaker[AsyncSession], bot: Bot, settings: Settings, limit: int = BATCH
) -> dict[str, int]:
    """Sends one batch. Returns counters; safe to call from several workers at once."""
    async with session_scope(maker) as session:
        batch = await claim_due(session, limit)
    if not batch:
        return {"sent": 0, "failed": 0, "blocked": 0}

    sent = failed = blocked = 0
    delay = 1.0 / MESSAGES_PER_SECOND
    for notice in batch:
        body = render(notice["kind"], notice["payload"], notice["lang"])
        if not body:
            continue
        try:
            await bot.send_message(
                notice["tg_id"], body, reply_markup=keyboard(settings, notice["lang"])
            )
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
            async with session_scope(maker) as session:
                await session.execute(
                    text("UPDATE users SET bot_blocked = true WHERE id = :id").bindparams(
                        id=notice["user_id"]
                    )
                )
        except TelegramRetryAfter as exc:
            # Telegram said how long to wait; waiting exactly that long is the fix.
            await asyncio.sleep(exc.retry_after)
            failed += 1
            await _mark_failed(maker, notice["id"])
        except TelegramAPIError:
            failed += 1
            await _mark_failed(maker, notice["id"])
        await asyncio.sleep(delay)

    log.info("notify.delivered", sent=sent, failed=failed, blocked=blocked)
    return {"sent": sent, "failed": failed, "blocked": blocked}


async def _mark_failed(maker: async_sessionmaker[AsyncSession], notification_id: int) -> None:
    """A failed send goes back to 'failed', not 'queued': retrying forever is worse."""
    async with session_scope(maker) as session:
        await session.execute(
            text("UPDATE notifications SET status = 'failed' WHERE id = :id").bindparams(
                id=notification_id
            )
        )


# ── producers ─────────────────────────────────────────────────────────────────


async def queue_new_tracks(session: AsyncSession, since_hours: int = 6) -> int:
    """Tells people when the channels they subscribed to got new music.

    One notification per user per channel per window, and only for channels that got
    enough new tracks to be worth a ping.
    """
    rows = await session.execute(
        text(
            """
        SELECT uc.user_id, c.id AS channel_id,
               coalesce(c.title, '@' || c.username) AS channel, count(*) AS new_tracks
          FROM channel_tracks ct
          JOIN channels c ON c.id = ct.channel_id
          JOIN user_channels uc ON uc.channel_id = c.id
          JOIN tracks t ON t.id = ct.track_id
         WHERE t.created_at > now() - make_interval(hours => :hours)
           AND NOT t.hidden AND c.status = 'active'
         GROUP BY 1, 2, 3
        HAVING count(*) >= 3
        """
        ).bindparams(hours=since_hours)
    )
    window = datetime.now(UTC).strftime("%Y%m%d%H")
    notices = [
        Notice(
            user_id=row.user_id,
            kind="new_tracks",
            payload={"channel": row.channel, "channel_id": row.channel_id, "count": row.new_tracks},
            dedup_key=f"new:{row.channel_id}:{window}",
        )
        for row in rows
    ]
    return await enqueue(session, notices)


async def queue_weekly_digest(session: AsyncSession) -> int:
    """A weekly "here is what you missed" for people who have not opened the app."""
    rows = await session.execute(
        text(
            """
        SELECT u.id,
               count(DISTINCT t.id) AS tracks,
               count(DISTINCT c.id) AS channels
          FROM users u
          JOIN user_channels uc ON uc.user_id = u.id
          JOIN channels c ON c.id = uc.channel_id
          JOIN channel_tracks ct ON ct.channel_id = c.id
          JOIN tracks t ON t.id = ct.track_id AND NOT t.hidden
         WHERE t.created_at > now() - interval '7 days'
           AND u.last_seen_at < now() - interval '3 days'
           AND NOT u.bot_blocked AND NOT u.is_banned
         GROUP BY 1
        HAVING count(DISTINCT t.id) >= 5
        """
        )
    )
    week = datetime.now(UTC).strftime("%G-W%V")
    notices = [
        Notice(
            user_id=row.id,
            kind="digest",
            payload={"tracks": row.tracks, "channels": row.channels},
            dedup_key=f"digest:{week}",
        )
        for row in rows
    ]
    return await enqueue(session, notices)


async def queue_discover_ready(session: AsyncSession, user_ids: list[int]) -> int:
    """Called by the mixes job once a user's Discover Weekly has been rebuilt."""
    week = datetime.now(UTC).strftime("%G-W%V")
    return await enqueue(
        session,
        [
            Notice(user_id=user_id, kind="discover_ready", payload={}, dedup_key=f"dw:{week}")
            for user_id in user_ids
        ],
    )


# ── preferences ───────────────────────────────────────────────────────────────


async def get_prefs(session: AsyncSession, user_id: int) -> dict[str, bool]:
    row = (
        await session.execute(
            text(
                "SELECT notification_prefs, tz_offset_minutes FROM users WHERE id = :id"
            ).bindparams(id=user_id)
        )
    ).one_or_none()
    stored = (row.notification_prefs if row else None) or {}
    return {kind: wants(stored, kind) for kind in KINDS}


async def set_prefs(
    session: AsyncSession, user_id: int, changes: dict[str, bool], tz_offset_minutes: int | None
) -> dict[str, bool]:
    unknown = set(changes) - set(KINDS)
    if unknown:
        raise InvalidInput("unknown notification kind", kinds=sorted(unknown))
    if tz_offset_minutes is not None and not -720 <= tz_offset_minutes <= 840:
        raise InvalidInput("timezone offset out of range")

    await session.execute(
        text(
            "UPDATE users SET notification_prefs = notification_prefs || CAST(:changes AS jsonb),"
            " tz_offset_minutes = coalesce(:tz, tz_offset_minutes) WHERE id = :id"
        ).bindparams(id=user_id, changes=json.dumps(changes), tz=tz_offset_minutes)
    )
    return await get_prefs(session, user_id)


async def prune(session: AsyncSession, keep_days: int = 30) -> int:
    result = await session.execute(
        text(
            "DELETE FROM notifications WHERE created_at < now() - make_interval(days => :days)"
            " RETURNING 1"
        ).bindparams(days=keep_days)
    )
    return len(result.all())
