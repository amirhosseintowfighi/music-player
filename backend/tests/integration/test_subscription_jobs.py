"""The daily subscription job: reminders, grace, downgrade and stale card reviews."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Payment, Subscription, SubscriptionNotice, User
from app.services import plans, subscriptions
from app.workers import jobs
from tests.integration.test_bot import RecordingSession
from tests.integration.test_payments import make_user


class BlockedSession(RecordingSession):
    """A user who blocked the bot: the job must carry on and not retry them."""

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:  # noqa: ASYNC109
        self.calls.append(method)
        raise TelegramForbiddenError(method=method, message="bot was blocked by the user")


@pytest.fixture
def ctx(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from app.db import make_sessionmaker

    recording = RecordingSession()
    monkeypatch.setattr(jobs, "build_bot", lambda settings: Bot(token=TOKEN, session=recording))
    return {"sessionmaker": make_sessionmaker(engine), "session": recording}


TOKEN = "123456:TEST-token_for-tests"


async def expiring_user(session: AsyncSession, tg_id: int, days: float) -> int:
    user = await make_user(session, tg_id=tg_id)
    subscription = await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    subscription_id = int(subscription.id)
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(expires_at=datetime.now(UTC) + timedelta(days=days))
    )
    await session.commit()
    return subscription_id


async def test_daily_job_sends_each_reminder_once(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    subscription_id = await expiring_user(session, 8101, days=2.5)
    result = await jobs.subscriptions_daily(ctx)
    assert result["notices"] == 2  # the 7- and 3-day windows both apply

    again = await jobs.subscriptions_daily(ctx)
    assert again["notices"] == 0

    session.expire_all()
    kinds = (
        await session.scalars(
            select(SubscriptionNotice.kind).where(
                SubscriptionNotice.subscription_id == subscription_id
            )
        )
    ).all()
    assert sorted(kinds) == ["d3", "d7"]
    plans.clear_caches()


async def test_daily_job_moves_to_grace_then_downgrades(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    user = await make_user(session, tg_id=8102)
    user_id = int(user.id)
    subscription = await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    subscription_id = int(subscription.id)
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(
            started_at=datetime.now(UTC) - timedelta(days=31),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await session.commit()

    assert (await jobs.subscriptions_daily(ctx))["graced"] == 1
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(grace_until=datetime.now(UTC) - timedelta(minutes=1))
    )
    await session.commit()
    assert (await jobs.subscriptions_daily(ctx))["downgraded"] == 1

    session.expire_all()
    stored = await session.get(User, user_id)
    assert stored is not None
    assert stored.plan_code == "free"
    plans.clear_caches()


async def test_a_blocked_user_does_not_block_the_job(
    session: AsyncSession, ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await expiring_user(session, 8103, days=0.5)
    blocked = BlockedSession()
    monkeypatch.setattr(jobs, "build_bot", lambda settings: Bot(token=TOKEN, session=blocked))

    result = await jobs.subscriptions_daily(ctx)
    assert result["notices"] == 0  # nothing was delivered
    assert len(blocked.calls) == 3  # but all three windows were attempted

    # The notice is still recorded, so the next run does not hammer a blocked user.
    assert (await jobs.subscriptions_daily(ctx))["notices"] == 0
    assert len(blocked.calls) == 3
    plans.clear_caches()


async def test_daily_job_expires_unanswered_card_payments(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    user = await make_user(session, tg_id=8104)
    await session.execute(
        text("UPDATE payment_providers SET is_enabled = true WHERE code = 'card2card'")
    )
    payment = Payment(
        user_id=user.id,
        provider="card2card",
        plan_code="pro_monthly",
        amount=1_490_000,
        currency="IRR",
        status="pending_review",
        review_due_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(payment)
    await session.commit()

    result = await jobs.subscriptions_daily(ctx)
    assert result["expired_reviews"] == 1
    session.expire_all()
    rows = await session.execute(text("SELECT status FROM payments"))
    assert [row[0] for row in rows] == ["expired"]
    await session.execute(text("UPDATE payment_providers SET is_enabled = (code = 'stars')"))
    await session.commit()
