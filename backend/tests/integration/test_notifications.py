"""Notifications: preferences, quiet hours, dedup and delivery.

The failure mode worth testing is not "no message arrived" — it is "the same message
arrived three times at 4am". So: dedup, quiet hours in the user's own offset, muted
kinds, and a blocked user being marked rather than retried.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInput
from app.models import User
from app.services import notifications
from app.services.ingest import ingest_items
from tests.conftest import BOT_TOKEN, bearer, login
from tests.integration.helpers import item, make_channel, subscribe

MORNING = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)  # 12:30 in Tehran
TEHRAN = 210


def daytime_offset() -> int:
    """An offset that makes *right now* local noon.

    Delivery tests must not depend on what time the suite happens to run: without
    this, a run after 23:00 Tehran would legitimately schedule everything for the
    morning and look like a bug.
    """
    return (12 - datetime.now(UTC).hour) * 60


async def make_user(session: AsyncSession, tg_id: int, **values: Any) -> User:
    user = User(tg_id=tg_id, first_name=f"U{tg_id}", referral_code=f"rn{tg_id}", **values)
    session.add(user)
    await session.flush()
    return user


async def queued(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT kind, status, dedup_key, send_after FROM notifications"
            " WHERE user_id = :id ORDER BY id"
        ).bindparams(id=user_id)
    )
    return [dict(row._mapping) for row in rows]


# ── quiet hours ───────────────────────────────────────────────────────────────


def test_quiet_hours_are_evaluated_in_the_users_own_offset() -> None:
    # 21:00 UTC is 00:30 in Tehran — the middle of the night, so it waits for morning.
    night = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
    moved = notifications.next_allowed(night, TEHRAN)
    assert moved > night
    assert (moved + timedelta(minutes=TEHRAN)).hour == notifications.QUIET_END_HOUR

    # The same instant is 21:00 for a user at UTC+0, which is still inside the evening,
    # so it goes out immediately for them.
    assert notifications.next_allowed(night, 0) == night
    assert notifications.next_allowed(datetime(2026, 9, 18, 12, 0, tzinfo=UTC), 0) == datetime(
        2026, 9, 18, 12, 0, tzinfo=UTC
    )


def test_a_daytime_notification_is_not_delayed() -> None:
    assert notifications.next_allowed(MORNING, TEHRAN) == MORNING


def test_early_morning_waits_for_the_same_morning_not_tomorrow() -> None:
    # 03:00 UTC = 06:30 Tehran: before the boundary, so it goes out at 09:00 local.
    early = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
    moved = notifications.next_allowed(early, TEHRAN)
    assert moved.date() == early.date()
    assert (moved + timedelta(minutes=TEHRAN)).hour == notifications.QUIET_END_HOUR


# ── preferences and dedup ─────────────────────────────────────────────────────


async def test_defaults_are_on_and_editable(session: AsyncSession) -> None:
    user = await make_user(session, 82001)
    assert await notifications.get_prefs(session, user.id) == dict.fromkeys(
        notifications.KINDS, True
    )

    updated = await notifications.set_prefs(session, user.id, {"digest": False}, None)
    assert updated["digest"] is False
    assert updated["new_tracks"] is True  # untouched kinds keep their default

    with pytest.raises(InvalidInput):
        await notifications.set_prefs(session, user.id, {"nope": True}, None)
    with pytest.raises(InvalidInput):
        await notifications.set_prefs(session, user.id, {}, 5000)


async def test_a_muted_kind_is_never_queued(session: AsyncSession) -> None:
    user = await make_user(session, 82002)
    await notifications.set_prefs(session, user.id, {"digest": False}, None)

    count = await notifications.enqueue(
        session,
        [
            notifications.Notice(user.id, "digest", {"tracks": 5, "channels": 1}),
            notifications.Notice(user.id, "new_tracks", {"count": 3, "channel": "X"}),
        ],
    )
    assert count == 1
    assert [row["kind"] for row in await queued(session, user.id)] == ["new_tracks"]


async def test_dedup_key_stops_a_repeated_producer(session: AsyncSession) -> None:
    user = await make_user(session, 82003)
    notice = notifications.Notice(user.id, "new_tracks", {"count": 3}, dedup_key="new:1:x")

    assert await notifications.enqueue(session, [notice]) == 1
    assert await notifications.enqueue(session, [notice]) == 0
    assert len(await queued(session, user.id)) == 1


async def test_blocked_and_banned_users_are_skipped(session: AsyncSession) -> None:
    blocked = await make_user(session, 82004, bot_blocked=True)
    banned = await make_user(session, 82005, is_banned=True)
    fine = await make_user(session, 82006)

    count = await notifications.enqueue(
        session,
        [
            notifications.Notice(blocked.id, "system", {"text": "hi"}),
            notifications.Notice(banned.id, "system", {"text": "hi"}),
            notifications.Notice(fine.id, "system", {"text": "hi"}),
        ],
    )
    assert count == 1


async def test_an_unknown_kind_is_rejected(session: AsyncSession) -> None:
    user = await make_user(session, 82007)
    with pytest.raises(InvalidInput):
        await notifications.enqueue(session, [notifications.Notice(user.id, "spam", {})])


async def test_a_night_time_notice_is_scheduled_for_the_morning(
    session: AsyncSession,
) -> None:
    user = await make_user(session, 82008, tz_offset_minutes=TEHRAN)
    night = datetime.now(UTC).replace(hour=21, minute=30)
    await notifications.enqueue(
        session,
        [notifications.Notice(user.id, "system", {"text": "hi"}, send_after=night)],
    )
    rows = await queued(session, user.id)
    assert rows[0]["send_after"] > night


# ── claiming and delivery ─────────────────────────────────────────────────────


async def test_claim_due_ignores_future_rows(session: AsyncSession) -> None:
    user = await make_user(session, 82010, tz_offset_minutes=daytime_offset())
    await notifications.enqueue(
        session,
        [
            notifications.Notice(user.id, "system", {"text": "now"}, dedup_key="a"),
            notifications.Notice(
                user.id,
                "system",
                {"text": "later"},
                dedup_key="b",
                send_after=datetime.now(UTC) + timedelta(days=1),
            ),
        ],
    )
    claimed = await notifications.claim_due(session)
    assert [entry["payload"]["text"] for entry in claimed] == ["now"]
    # Claiming marks them, so a second worker gets nothing.
    assert await notifications.claim_due(session) == []


async def test_delivery_sends_renders_and_marks_blocked(
    session: AsyncSession, engine: Any, settings: Any
) -> None:
    from aiogram import Bot
    from aiogram.exceptions import TelegramForbiddenError

    from app.db import make_sessionmaker
    from tests.integration.test_bot import RecordingSession

    class Blocking(RecordingSession):
        async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:  # noqa: ASYNC109
            self.calls.append(method)
            if len(self.calls) == 1:
                raise TelegramForbiddenError(method=method, message="blocked")
            return True

    first = await make_user(session, 82011, tz_offset_minutes=daytime_offset())
    second = await make_user(session, 82012, tz_offset_minutes=daytime_offset())
    first_id, second_id = first.id, second.id
    await notifications.enqueue(
        session,
        [
            notifications.Notice(first_id, "new_tracks", {"count": 4, "channel": "پاپ"}),
            notifications.Notice(second_id, "discover_ready", {}),
        ],
    )
    await session.commit()

    recording = Blocking()
    bot = Bot(token=BOT_TOKEN, session=recording)
    notifications.MESSAGES_PER_SECOND = 1000
    result = await notifications.deliver(make_sessionmaker(engine), bot, settings)
    await bot.session.close()

    assert result == {"sent": 1, "failed": 0, "blocked": 1}
    session.expire_all()
    flagged = await session.execute(
        text("SELECT count(*) FROM users WHERE bot_blocked AND id = :id").bindparams(id=first_id)
    )
    assert flagged.scalar_one() == 1
    # The second message carries the rendered Persian copy, not a payload dump.
    assert "کشف هفتگی" in getattr(recording.calls[-1], "text", "")


async def test_nothing_due_is_a_cheap_no_op(
    session: AsyncSession, engine: Any, settings: Any
) -> None:
    from aiogram import Bot

    from app.db import make_sessionmaker
    from tests.integration.test_bot import RecordingSession

    recording = RecordingSession()
    bot = Bot(token=BOT_TOKEN, session=recording)
    result = await notifications.deliver(make_sessionmaker(engine), bot, settings)
    await bot.session.close()
    assert result == {"sent": 0, "failed": 0, "blocked": 0}
    assert recording.calls == []


# ── producers ─────────────────────────────────────────────────────────────────


async def test_new_tracks_producer_needs_enough_new_music(session: AsyncSession) -> None:
    channel = await make_channel(session, "newsch")
    user = await make_user(session, 82020)
    await subscribe(session, user.id, channel.id)

    await ingest_items(session, channel, [item("تک", "معین", msg=9001)], bot_id=None)
    assert await notifications.queue_new_tracks(session) == 0  # one track is not news

    await ingest_items(
        session,
        channel,
        [item(f"آهنگ {i}", "گوگوش", msg=9010 + i) for i in range(4)],
        bot_id=None,
    )
    assert await notifications.queue_new_tracks(session) == 1
    rows = await queued(session, user.id)
    assert rows[0]["kind"] == "new_tracks"

    # Running the job again in the same hour must not tell them twice.
    assert await notifications.queue_new_tracks(session) == 0


async def test_digest_only_targets_people_who_drifted_away(session: AsyncSession) -> None:
    channel = await make_channel(session, "digestch")
    active = await make_user(session, 82021)
    lapsed = await make_user(session, 82022)
    await subscribe(session, active.id, channel.id)
    await subscribe(session, lapsed.id, channel.id)
    await session.execute(
        text(
            "UPDATE users SET last_seen_at = now() - interval '10 days' WHERE id = :id"
        ).bindparams(id=lapsed.id)
    )
    await ingest_items(
        session,
        channel,
        [item(f"هفته {i}", "هایده", msg=9100 + i) for i in range(6)],
        bot_id=None,
    )

    assert await notifications.queue_weekly_digest(session) == 1
    assert await queued(session, active.id) == []
    assert (await queued(session, lapsed.id))[0]["kind"] == "digest"


async def test_prune_drops_old_rows(session: AsyncSession) -> None:
    user = await make_user(session, 82023)
    await notifications.enqueue(session, [notifications.Notice(user.id, "system", {"text": "x"})])
    await session.execute(text("UPDATE notifications SET created_at = now() - interval '60 days'"))
    assert await notifications.prune(session) == 1


# ── API ───────────────────────────────────────────────────────────────────────


async def test_preference_endpoints(client: httpx.AsyncClient) -> None:
    token = await login(client, 82030)

    current = await client.get("/v1/me/notifications", headers=bearer(token))
    assert current.json()["prefs"]["digest"] is True

    updated = await client.put(
        "/v1/me/notifications",
        json={"prefs": {"digest": False}, "tz_offset_minutes": 0},
        headers=bearer(token),
    )
    assert updated.json()["prefs"]["digest"] is False

    bad = await client.put(
        "/v1/me/notifications", json={"prefs": {"nope": True}}, headers=bearer(token)
    )
    assert bad.status_code == 422


async def test_notification_jobs_run_end_to_end(
    session: AsyncSession, engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiogram import Bot

    from app.db import make_sessionmaker
    from app.workers import jobs
    from tests.integration.test_bot import RecordingSession

    channel = await make_channel(session, "jobch")
    user = await make_user(session, 82040, tz_offset_minutes=daytime_offset())
    await subscribe(session, user.id, channel.id)
    await ingest_items(
        session,
        channel,
        [item(f"جاب {i}", "معین", msg=9200 + i) for i in range(4)],
        bot_id=None,
    )
    await session.commit()

    ctx = {"sessionmaker": make_sessionmaker(engine)}
    assert (await jobs.notify_new_tracks(ctx))["queued"] == 1

    recording = RecordingSession()
    monkeypatch.setattr(jobs, "build_bot", lambda settings: Bot(token=BOT_TOKEN, session=recording))
    notifications.MESSAGES_PER_SECOND = 1000
    result = await jobs.deliver_notifications(ctx)
    assert result["sent"] == 1
    assert len(recording.calls) == 1
