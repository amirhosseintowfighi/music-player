"""Phase 6: checkout, callbacks, activation, upgrades, trials, referrals and expiry.

The money paths are the ones that must never be "approximately" right, so every rule
from §6 of the brief has a test here: server-side verification, idempotent callbacks,
amount tampering, pro-rated upgrades, grace then downgrade, discounts, trial and
referral rewards, and an audit_log row for every financial event.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import Conflict, Forbidden, InvalidInput
from app.models import DiscountCode, Payment, Referral, Subscription, User
from app.services import plans, subscriptions
from tests.conftest import bearer, login


def gateway(routes: dict[str, Any]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, payload in routes.items():
            if fragment in str(request.url):
                return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected call to {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


ZARINPAL = {
    "request.json": {"data": {"code": 100, "authority": "A1"}},
    "verify.json": {"data": {"code": 100, "ref_id": 5}},
}
STARS = {"createInvoiceLink": {"ok": True, "result": "https://t.me/$inv"}}


@pytest.fixture(autouse=True)
async def _reset_providers(session: AsyncSession) -> AsyncIterator[None]:
    """payment_providers is reference data, so _clean_db keeps it between tests."""
    yield
    await session.rollback()
    await session.execute(text("UPDATE payment_providers SET is_enabled = (code = 'stars')"))
    await session.commit()


async def enable(session: AsyncSession, *codes: str) -> None:
    await session.execute(
        text("UPDATE payment_providers SET is_enabled = true WHERE code = ANY(:c)").bindparams(
            c=list(codes)
        )
    )


async def make_admin(session: AsyncSession) -> int:
    row = await session.execute(
        text(
            "INSERT INTO admin_users (tg_id, role, permissions, is_active) "
            "VALUES (9001, 'owner', ARRAY['*'], true) RETURNING id"
        )
    )
    return int(row.scalar_one())


async def make_user(session: AsyncSession, tg_id: int = 5001, **values: Any) -> User:
    user = User(
        tg_id=tg_id,
        first_name="Pardakht",
        referral_code=f"ref{tg_id}",
        **values,
    )
    session.add(user)
    await session.flush()
    return user


async def audit_actions(session: AsyncSession, entity_id: int | str) -> list[str]:
    rows = await session.execute(
        text("SELECT action FROM audit_log WHERE entity_id = :e ORDER BY id").bindparams(
            e=str(entity_id)
        )
    )
    return [row[0] for row in rows]


# ── checkout ──────────────────────────────────────────────────────────────────


async def test_checkout_creates_pending_payment(
    session: AsyncSession, paid_settings: Settings
) -> None:
    await enable(session, "zarinpal")
    user = await make_user(session)
    async with gateway(ZARINPAL) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal"
        )
    assert checkout.amount == 1_490_000
    assert checkout.invoice.url == "https://sandbox.zarinpal.com/pg/StartPay/A1"
    payment = await session.get(Payment, checkout.payment_id)
    assert payment is not None
    assert payment.status == "pending"
    assert payment.provider_ref == "A1"
    assert "payment.created" in await audit_actions(session, payment.id)


async def test_checkout_rejects_unknown_plan_and_disabled_provider(
    session: AsyncSession, paid_settings: Settings
) -> None:
    user = await make_user(session)
    async with gateway(ZARINPAL) as http:
        with pytest.raises(InvalidInput, match="provider"):
            await subscriptions.start_checkout(
                session, http, paid_settings, user, "pro_monthly", "zarinpal"
            )
        await enable(session, "zarinpal")
        with pytest.raises(InvalidInput, match="plan"):
            await subscriptions.start_checkout(
                session, http, paid_settings, user, "nope", "zarinpal"
            )
        # The free plan has no period, so it can never be bought.
        with pytest.raises(InvalidInput, match="plan"):
            await subscriptions.start_checkout(
                session, http, paid_settings, user, "free", "zarinpal"
            )
    # A plan that has no price in the gateway's currency is refused too.
    with pytest.raises(InvalidInput, match="price"):
        await subscriptions.price_for(session, "pro_monthly", "USD")


async def test_failed_invoice_marks_the_payment_failed(
    session: AsyncSession, paid_settings: Settings
) -> None:
    await enable(session, "zarinpal")
    user = await make_user(session)
    async with gateway({"request.json": {"errors": {"code": -9}}}) as http:
        with pytest.raises(Exception, match="zarinpal"):
            await subscriptions.start_checkout(
                session, http, paid_settings, user, "pro_monthly", "zarinpal"
            )
    payment = (await session.scalars(select(Payment))).one()
    assert payment.status == "failed"


# ── settlement ────────────────────────────────────────────────────────────────


async def test_callback_activates_once_and_is_idempotent(
    session: AsyncSession, paid_settings: Settings
) -> None:
    await enable(session, "zarinpal")
    user = await make_user(session)
    async with gateway(ZARINPAL) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal"
        )
        callback = {"Authority": "A1", "Status": "OK"}
        first = await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", callback
        )
        second = await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", callback
        )

    assert first.id == second.id == checkout.payment_id
    assert first.status == "paid"
    live = (await session.scalars(select(Subscription))).all()
    assert len(live) == 1
    assert live[0].status == "active"
    await session.refresh(user)
    assert user.plan_code == "pro_monthly"
    assert user.premium_until is not None
    assert await audit_actions(session, first.id) == ["payment.created", "payment.paid"]


async def test_amount_mismatch_fails_the_payment(
    session: AsyncSession, paid_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.payments.base import VerificationResult
    from app.services.payments.iranian import Zarinpal

    await enable(session, "zarinpal")
    user = await make_user(session)

    async def lying_verify(self: Zarinpal, callback: dict[str, Any]) -> VerificationResult:
        return VerificationResult(True, provider_ref="A1", amount=1, detail="")

    async with gateway(ZARINPAL) as http:
        await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal"
        )
        monkeypatch.setattr(Zarinpal, "verify", lying_verify)
        payment = await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", {"Authority": "A1", "Status": "OK"}
        )
    assert payment.status == "failed"
    assert payment.note is not None
    assert "mismatch" in payment.note
    assert (await session.scalars(select(Subscription))).first() is None


async def test_cancelled_payment_stays_unpaid(
    session: AsyncSession, paid_settings: Settings
) -> None:
    await enable(session, "zarinpal")
    user = await make_user(session)
    async with gateway(ZARINPAL) as http:
        await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal"
        )
        payment = await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", {"Authority": "A1", "Status": "NOK"}
        )
    assert payment.status == "failed"
    await session.refresh(user)
    assert user.plan_code == "free"
    assert "payment.failed" in await audit_actions(session, payment.id)


# ── activation rules ──────────────────────────────────────────────────────────


async def test_renewal_extends_from_the_current_expiry(session: AsyncSession) -> None:
    user = await make_user(session)
    first = await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    second = await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    assert second.expires_at - first.expires_at == timedelta(days=30)
    statuses = sorted(s.status for s in (await session.scalars(select(Subscription))).all())
    assert statuses == ["active", "canceled"]  # only one is live


async def test_upgrade_credits_the_unused_days(session: AsyncSession) -> None:
    """Monthly -> yearly keeps the value already paid instead of discarding it."""
    user = await make_user(session)
    await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    upgraded = await subscriptions.activate(session, user.id, "pro_yearly", source="admin")
    plain_year = datetime.now(UTC) + timedelta(days=365)
    assert upgraded.expires_at > plain_year
    # 30 unused monthly days (1.49M rial) buy ~36 yearly days at the yearly daily rate.
    assert upgraded.expires_at < plain_year + timedelta(days=40)


async def test_only_one_live_subscription_per_user(session: AsyncSession) -> None:
    user = await make_user(session)
    await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    await subscriptions.activate(session, user.id, "pro_yearly", source="admin")
    live = (
        await session.scalars(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.status.in_(("active", "trialing", "grace")),
            )
        )
    ).all()
    assert len(live) == 1


# ── discounts ─────────────────────────────────────────────────────────────────


async def make_discount(session: AsyncSession, **values: Any) -> DiscountCode:
    code = DiscountCode(
        code=values.pop("code", "OFF20"),
        kind=values.pop("kind", "percent"),
        value=values.pop("value", 20),
        **values,
    )
    session.add(code)
    await session.flush()
    return code


async def test_percent_discount_applies_and_counts_once(
    session: AsyncSession, paid_settings: Settings
) -> None:
    await enable(session, "zarinpal")
    discount = await make_discount(session, max_uses=5)
    user = await make_user(session)
    async with gateway(ZARINPAL) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal", discount_code="OFF20"
        )
        assert checkout.amount == 1_192_000
        await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", {"Authority": "A1", "Status": "OK"}
        )
    await session.refresh(discount)
    assert discount.uses == 1


async def test_discount_rejected_when_used_expired_or_wrong_plan(session: AsyncSession) -> None:
    user = await make_user(session)
    await make_discount(session, code="OLD", expires_at=datetime.now(UTC) - timedelta(days=1))
    await make_discount(session, code="PLANONLY", plan_codes=["pro_yearly"])
    await make_discount(session, code="SPENT", max_uses=1, uses=1)
    for code in ("OLD", "PLANONLY", "SPENT", "NOPE"):
        with pytest.raises(InvalidInput):
            await subscriptions.apply_discount(
                session, user.id, code, 1_490_000, "pro_monthly", "IRR"
            )


async def test_fixed_discount_never_reaches_zero(session: AsyncSession) -> None:
    user = await make_user(session)
    await make_discount(session, code="BIG", kind="fixed", value=99_000_000, currency="IRR")
    amount, code = await subscriptions.apply_discount(
        session, user.id, "BIG", 1_490_000, "pro_monthly", "IRR"
    )
    assert amount == 1000
    assert code is not None


# ── trial, referral, gift ─────────────────────────────────────────────────────


async def test_trial_is_granted_once(session: AsyncSession) -> None:
    user = await make_user(session)
    trial = await subscriptions.start_trial(session, user.id)
    assert trial.status == "trialing"
    assert trial.expires_at - trial.started_at == timedelta(days=7)
    with pytest.raises(Forbidden, match="trial"):
        await subscriptions.start_trial(session, user.id)

    # Even with the flag cleared, a live subscription blocks a second trial.
    await session.execute(update(User).where(User.id == user.id).values(trial_used=False))
    with pytest.raises(Conflict):
        await subscriptions.start_trial(session, user.id)


async def test_referral_reward_is_paid_once(session: AsyncSession) -> None:
    referrer = await make_user(session, tg_id=6001)
    referee = await make_user(session, tg_id=6002, referred_by=referrer.id)
    session.add(Referral(referrer_id=referrer.id, referee_id=referee.id))
    await session.flush()

    assert await subscriptions.reward_referral(session, referee.id) is True
    assert await subscriptions.reward_referral(session, referee.id) is False
    live = await subscriptions.current(session, referrer.id)
    assert live is not None
    assert live.source == "referral"
    assert live.expires_at - live.started_at == timedelta(days=3)


async def test_gift_is_audited(session: AsyncSession) -> None:
    user = await make_user(session)
    admin = await make_admin(session)
    await subscriptions.gift(session, admin_id=admin, user_id=user.id, days=10)
    assert "subscription.gift" in await audit_actions(session, user.id)


# ── lifecycle ─────────────────────────────────────────────────────────────────


async def test_expiry_moves_to_grace_then_downgrades(session: AsyncSession) -> None:
    user = await make_user(session)
    subscription = await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription.id)
        .values(
            started_at=datetime.now(UTC) - timedelta(days=31),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )

    graced, downgraded = await subscriptions.expire_due(session)
    assert (graced, downgraded) == (1, 0)
    await session.refresh(user)
    assert user.plan_code == "pro_monthly"  # still premium during grace

    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription.id)
        .values(grace_until=datetime.now(UTC) - timedelta(minutes=1))
    )
    graced, downgraded = await subscriptions.expire_due(session)
    assert (graced, downgraded) == (0, 1)
    await session.refresh(user)
    assert user.plan_code == "free"
    assert user.premium_until is None
    assert "subscription.expired" in await audit_actions(session, user.id)


async def test_expiry_notices_are_sent_once_per_window(session: AsyncSession) -> None:
    user = await make_user(session)
    subscription = await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription.id)
        .values(expires_at=datetime.now(UTC) + timedelta(days=2, hours=12))
    )
    due = await subscriptions.due_for_notice(session)
    assert sorted(days for _, _, days, _ in due) == [3, 7]

    for subscription_id, _, days, _ in due:
        await subscriptions.mark_notice_sent(session, subscription_id, f"d{days}")
    assert await subscriptions.due_for_notice(session) == []


async def test_stale_card_reviews_expire(session: AsyncSession, paid_settings: Settings) -> None:
    await enable(session, "card2card")
    user = await make_user(session)
    async with gateway({}) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "card2card"
        )
    payment = await session.get(Payment, checkout.payment_id)
    assert payment is not None
    assert payment.status == "pending_review"
    assert checkout.invoice.kind == "instructions"

    assert await subscriptions.expire_stale_reviews(session) == []
    await session.execute(
        update(Payment)
        .where(Payment.id == payment.id)
        .values(review_due_at=datetime.now(UTC) - timedelta(hours=1))
    )
    assert await subscriptions.expire_stale_reviews(session) == [user.id]


async def test_admin_approval_activates_card_payment(
    session: AsyncSession, paid_settings: Settings
) -> None:
    admin = await make_admin(session)
    await enable(session, "card2card")
    user = await make_user(session)
    async with gateway({}) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "card2card"
        )
    payment = await subscriptions.locked_payment(session, checkout.payment_id)
    await subscriptions.attach_receipt(session, payment, "AgACPhoto")
    assert payment.receipt_file_id == "AgACPhoto"

    await subscriptions.mark_paid(session, payment, provider_ref=None, admin_id=admin)
    await session.flush()
    await session.refresh(user)
    assert user.plan_code == "pro_monthly"
    assert payment.reviewed_by == admin


async def test_admin_rejection_keeps_the_user_free(
    session: AsyncSession, paid_settings: Settings
) -> None:
    admin = await make_admin(session)
    await enable(session, "card2card")
    user = await make_user(session)
    async with gateway({}) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "card2card"
        )
    payment = await subscriptions.reject_payment(
        session, checkout.payment_id, admin, "blurry photo"
    )
    assert payment.status == "rejected"
    await session.refresh(user)
    assert user.plan_code == "free"
    with pytest.raises(Conflict):
        await subscriptions.refund(session, httpx.AsyncClient(), paid_settings, payment.id)


async def test_refund_cancels_the_subscription(
    session: AsyncSession, paid_settings: Settings
) -> None:
    await enable(session, "zarinpal")
    user = await make_user(session)
    admin = await make_admin(session)
    async with gateway(ZARINPAL) as http:
        checkout = await subscriptions.start_checkout(
            session, http, paid_settings, user, "pro_monthly", "zarinpal"
        )
        await subscriptions.settle_callback(
            session, http, paid_settings, "zarinpal", {"Authority": "A1", "Status": "OK"}
        )
        payment = await subscriptions.refund(
            session, http, paid_settings, checkout.payment_id, admin_id=admin, call_provider=False
        )
    assert payment.status == "refunded"
    await session.refresh(user)
    assert user.plan_code == "free"
    assert await subscriptions.current(session, user.id) is None
    assert "payment.refunded" in await audit_actions(session, payment.id)


# ── API ───────────────────────────────────────────────────────────────────────


async def test_plans_endpoint_lists_db_plans_and_providers(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await enable(session, "zarinpal")
    await session.commit()
    token = await login(client, 7001)
    resp = await client.get("/v1/plans", headers=bearer(token))
    assert resp.status_code == 200
    body = resp.json()
    codes = [p["code"] for p in body["plans"]]
    assert codes == ["free", "pro_monthly", "pro_yearly"]
    assert body["plans"][1]["prices"]["IRR"] == 1490000
    assert {p["code"] for p in body["providers"]} == {"stars", "zarinpal"}
    assert {p["kind"] for p in body["providers"]} == {"telegram_invoice", "redirect"}
    assert body["trial_available"] is True
    assert body["trial_days"] == 7
    plans.clear_caches()


async def test_checkout_and_subscription_endpoints(
    client: httpx.AsyncClient, session: AsyncSession, app_state: Any
) -> None:
    await enable(session, "stars")
    await session.commit()
    app_state.http = gateway(STARS)
    token = await login(client, 7002)

    resp = await client.post(
        "/v1/payments/checkout",
        json={"plan_code": "pro_monthly", "provider": "stars"},
        headers=bearer(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "telegram_invoice"
    assert body["url"] == "https://t.me/$inv"
    assert body["amount"] == 150
    assert body["currency"] == "XTR"

    mine = await client.get("/v1/me/payments", headers=bearer(token))
    assert [p["status"] for p in mine.json()] == ["pending"]

    sub = await client.get("/v1/me/subscription", headers=bearer(token))
    assert sub.json()["status"] == "free"

    trial = await client.post("/v1/payments/trial", headers=bearer(token))
    assert trial.status_code == 200
    assert trial.json()["status"] == "trialing"
    assert trial.json()["days_left"] in (6, 7)
    plans.clear_caches()


async def test_discount_preview_endpoint(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_discount(session, code="HALF", value=50)
    await session.commit()
    token = await login(client, 7003)
    ok = await client.get(
        "/v1/payments/discount",
        params={"code": "HALF", "plan_code": "pro_monthly"},
        headers=bearer(token),
    )
    assert ok.json() == {
        "valid": True,
        "amount": 745000,
        "original_amount": 1490000,
        "discount_amount": 745000,
        "currency": "IRR",
        "reason": None,
    }
    bad = await client.get(
        "/v1/payments/discount",
        params={"code": "NOPE", "plan_code": "pro_monthly"},
        headers=bearer(token),
    )
    assert bad.json()["valid"] is False
    assert bad.json()["reason"] == "invalid_code"


async def test_callback_endpoint_redirects_into_the_mini_app(
    client: httpx.AsyncClient, session: AsyncSession, app_state: Any
) -> None:
    await enable(session, "zarinpal")
    await session.commit()
    app_state.http = gateway(ZARINPAL)
    app_state.settings = app_state.settings.model_copy(
        update={"public_api_url": "https://api.example.test"}
    )
    token = await login(client, 7004)
    from pydantic import SecretStr

    app_state.settings = app_state.settings.model_copy(
        update={"zarinpal_merchant_id": SecretStr("merchant-1")}
    )

    checkout = await client.post(
        "/v1/payments/checkout",
        json={"plan_code": "pro_monthly", "provider": "zarinpal"},
        headers=bearer(token),
    )
    assert checkout.status_code == 200, checkout.text

    resp = await client.get(
        "/v1/payments/callback/zarinpal", params={"Authority": "A1", "Status": "OK"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "https://t.me/tmusic_test_bot/app?startapp=pay_ok"

    sub = await client.get("/v1/me/subscription", headers=bearer(token))
    assert sub.json()["plan_code"] == "pro_monthly"
    assert sub.json()["status"] == "active"
    plans.clear_caches()


async def test_unknown_callback_never_500s(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await enable(session, "zarinpal")
    await session.commit()
    resp = await client.post("/v1/payments/callback/zarinpal", json={"Authority": "ghost"})
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("pay_failed")


async def test_checkout_is_not_reachable_without_a_token(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/payments/checkout", json={"plan_code": "pro_monthly", "provider": "stars"}
    )
    assert resp.status_code == 401


async def test_settings_are_read_as_json(session: AsyncSession) -> None:
    assert await subscriptions.get_setting(session, "grace_days", 0) == 3
    assert await subscriptions.get_setting(session, "missing_key", "fallback") == "fallback"
    await session.execute(
        text("UPDATE settings SET value = CAST(:v AS jsonb) WHERE key = 'trial_days'").bindparams(
            v=json.dumps(14)
        )
    )
    assert await subscriptions.get_setting(session, "trial_days", 7) == 14
