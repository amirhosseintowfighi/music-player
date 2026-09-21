"""Subscriptions: checkout, activation, upgrades, trials, referrals and expiry.

Rules that must hold no matter which gateway is involved (ADR-0008):

- **Idempotent settlement.** ``(provider, provider_ref)`` is unique, and activation
  runs inside one transaction that locks the user row, so a repeated callback cannot
  grant two subscriptions.
- **Server-side verification.** A payment only becomes ``paid`` after the provider
  confirms it (or, for card-to-card, after an admin approves it).
- **One live subscription per user**, enforced by a partial unique index; an upgrade
  converts the remaining value of the old plan into extra days (pro-rate).
- Every money event is written to ``audit_log``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.config import Settings
from app.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.models import (
    DiscountCode,
    DiscountRedemption,
    Payment,
    Plan,
    Referral,
    Subscription,
    SubscriptionNotice,
    User,
)
from app.services import payments as registry
from app.services import plans as plan_service
from app.services.payments.base import Invoice, InvoiceRequest, ProviderError
from tmusic_common.logging import get_logger

log = get_logger(__name__)

GRACE_DEFAULT_DAYS = 3
TRIAL_DEFAULT_DAYS = 7
REFERRAL_DEFAULT_DAYS = 3
REVIEW_TTL_HOURS = 24


@dataclass(frozen=True, slots=True)
class Checkout:
    payment_id: int
    public_id: str
    provider: str
    amount: int
    currency: str
    invoice: Invoice


async def get_setting(session: AsyncSession, key: str, default: Any) -> Any:
    row = await session.execute(text("SELECT value FROM settings WHERE key = :k").bindparams(k=key))
    found = row.scalar_one_or_none()
    if found is None:
        return default
    # asyncpg hands raw jsonb back as text when the query has no typed column.
    return json.loads(found) if isinstance(found, str) else found


async def audit(
    session: AsyncSession,
    actor_type: str,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_log (actor_type, actor_id, action, entity, entity_id, payload) "
            "VALUES (:at, :aid, :action, :entity, :eid, CAST(:payload AS jsonb))"
        ).bindparams(
            at=actor_type,
            aid=actor_id,
            action=action,
            entity=entity,
            eid=str(entity_id),
            payload=json.dumps(payload, default=str),
        )
    )


async def price_for(session: AsyncSession, plan_code: str, currency: str) -> tuple[Plan, int]:
    plan = await session.get(Plan, plan_code)
    if plan is None or not plan.is_active or plan.period_days is None:
        raise InvalidInput("unknown plan", plan=plan_code)
    amount = int(plan.prices.get(currency, 0))
    if amount <= 0:
        raise InvalidInput("plan has no price in this currency", plan=plan_code, currency=currency)
    return plan, amount


async def apply_discount(
    session: AsyncSession,
    user_id: int,
    code: str | None,
    amount: int,
    plan_code: str,
    currency: str,
) -> tuple[int, DiscountCode | None]:
    """Returns the discounted amount. Raises when the code is not usable."""
    if not code:
        return amount, None
    discount = (
        await session.scalars(select(DiscountCode).where(DiscountCode.code == code.strip()))
    ).one_or_none()
    now = datetime.now(UTC)
    if (
        discount is None
        or not discount.is_active
        or discount.starts_at > now
        or (discount.expires_at is not None and discount.expires_at <= now)
        or (discount.max_uses is not None and discount.uses >= discount.max_uses)
        or (discount.plan_codes and plan_code not in discount.plan_codes)
        or (discount.kind == "fixed" and discount.currency != currency)
    ):
        raise InvalidInput("discount code is not valid", reason="invalid_code")

    used = await session.scalar(
        select(func.count())
        .select_from(DiscountRedemption)
        .where(
            DiscountRedemption.discount_code_id == discount.id,
            DiscountRedemption.user_id == user_id,
        )
    )
    if (used or 0) >= discount.max_uses_per_user:
        raise InvalidInput("discount code already used", reason="already_used")

    if discount.kind == "percent":
        final = amount - (amount * int(discount.value)) // 100
    else:
        final = amount - int(discount.value)
    # Never go to zero: a free payment cannot be verified by a gateway.
    return max(final, 1000 if currency == "IRR" else 1), discount


async def start_checkout(
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    user: User,
    plan_code: str,
    provider_code: str,
    discount_code: str | None = None,
) -> Checkout:
    provider = await registry.build(session, provider_code, http, settings)
    plan, list_price = await price_for(session, plan_code, provider.currency)
    amount, discount = await apply_discount(
        session, user.id, discount_code, list_price, plan_code, provider.currency
    )

    payment = (
        await session.scalars(
            insert(Payment)
            .values(
                user_id=user.id,
                provider=provider_code,
                plan_code=plan_code,
                amount=amount,
                currency=provider.currency,
                discount_code_id=discount.id if discount else None,
                discount_amount=list_price - amount,
                status="created",
                review_due_at=(
                    datetime.now(UTC) + timedelta(hours=REVIEW_TTL_HOURS)
                    if provider_code == "card2card"
                    else None
                ),
            )
            .returning(Payment)
        )
    ).one()
    await session.flush()

    request = InvoiceRequest(
        payment_id=payment.id,
        public_id=str(payment.public_id),
        amount=amount,
        currency=provider.currency,
        plan_code=plan_code,
        title=plan.name_fa if user.lang == "fa" else plan.name_en,
        description=f"{plan.name_en} ({plan.period_days} days)",
        user_tg_id=user.tg_id,
        callback_url=f"{settings.public_api_url}/v1/payments/callback/{provider_code}",
    )
    try:
        invoice = await provider.create_invoice(request)
    except ProviderError as exc:
        payment.status = "failed"
        payment.note = str(exc)[:500]
        log.warning("payment.invoice_failed", provider=provider_code, error=str(exc))
        raise

    payment.status = "pending_review" if provider_code == "card2card" else "pending"
    if invoice.provider_ref:
        payment.provider_ref = invoice.provider_ref
    await audit(
        session,
        "user",
        user.id,
        "payment.created",
        "payment",
        str(payment.id),
        {"provider": provider_code, "amount": amount, "plan": plan_code},
    )
    await session.flush()
    return Checkout(
        payment_id=payment.id,
        public_id=str(payment.public_id),
        provider=provider_code,
        amount=amount,
        currency=provider.currency,
        invoice=invoice,
    )


async def locked_payment(session: AsyncSession, payment_id: int) -> Payment:
    payment = (
        await session.scalars(select(Payment).where(Payment.id == payment_id).with_for_update())
    ).one_or_none()
    if payment is None:
        raise NotFound("payment not found")
    return payment


async def settle_callback(
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    provider_code: str,
    callback: dict[str, Any],
    public_id: str | None = None,
) -> Payment:
    """Verifies a gateway callback and activates the subscription once."""
    provider = await registry.build(session, provider_code, http, settings)
    payment = await _find_payment(session, provider_code, callback, public_id)
    if payment.status == "paid":
        log.info("payment.duplicate_callback", payment_id=payment.id)
        return payment  # idempotent: the first callback already settled it

    payment = await locked_payment(session, payment.id)
    if payment.status == "paid":
        return payment

    result = await provider.verify({**callback, "amount": payment.amount})
    if not result.paid:
        payment.status = "failed"
        payment.note = result.detail[:500]
        metrics.PAYMENTS.labels(provider_code, "failed").inc()
        payment.raw_callback = _safe_callback(callback)
        await audit(
            session,
            "provider",
            None,
            "payment.failed",
            "payment",
            str(payment.id),
            {"provider": provider_code, "detail": result.detail},
        )
        return payment

    if result.amount is not None and result.amount != payment.amount:
        # A mismatched amount is never accepted silently.
        payment.status = "failed"
        payment.note = f"amount mismatch: expected {payment.amount}, got {result.amount}"
        log.error(
            "payment.amount_mismatch",
            payment_id=payment.id,
            expected=payment.amount,
            got=result.amount,
        )
        return payment

    await mark_paid(session, payment, provider_ref=result.provider_ref, detail=result.detail)
    return payment


async def _by_public_id(session: AsyncSession, value: str) -> Payment | None:
    try:
        public_id = uuid.UUID(str(value))
    except ValueError:
        return None  # a gateway may echo something that is not our id at all
    return (
        await session.scalars(select(Payment).where(Payment.public_id == public_id))
    ).one_or_none()


async def _find_payment(
    session: AsyncSession, provider_code: str, callback: dict[str, Any], public_id: str | None
) -> Payment:
    if public_id:
        found = await _by_public_id(session, public_id)
        if found is not None:
            return found
    for key in ("order_id", "payload", "invoice_payload"):
        value = callback.get(key)
        if value:
            found = await _by_public_id(session, str(value))
            if found is not None:
                return found
    for key in ("Authority", "authority", "id", "trans_id"):
        value = callback.get(key)
        if value:
            found = (
                await session.scalars(
                    select(Payment).where(
                        Payment.provider == provider_code, Payment.provider_ref == str(value)
                    )
                )
            ).one_or_none()
            if found is not None:
                return found
    raise NotFound("payment not found for this callback")


def _safe_callback(callback: dict[str, Any]) -> dict[str, Any]:
    """Keeps an audit copy without anything card-like."""
    drop = {"card_no", "cardNumber", "hashed_card_no", "card_hash"}
    return {k: v for k, v in callback.items() if k not in drop}


async def mark_paid(
    session: AsyncSession,
    payment: Payment,
    *,
    provider_ref: str | None,
    detail: str = "",
    admin_id: int | None = None,
) -> Subscription:
    """Marks a payment paid and activates (or extends) the subscription."""
    payment.status = "paid"
    payment.paid_at = datetime.now(UTC)
    if provider_ref:
        payment.provider_ref = provider_ref
    if detail:
        payment.note = detail[:500]
    if admin_id is not None:
        payment.reviewed_by = admin_id
        payment.reviewed_at = datetime.now(UTC)

    if payment.discount_code_id:
        await session.execute(
            insert(DiscountRedemption)
            .values(
                discount_code_id=payment.discount_code_id,
                user_id=payment.user_id,
                payment_id=payment.id,
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            update(DiscountCode)
            .where(DiscountCode.id == payment.discount_code_id)
            .values(uses=DiscountCode.uses + 1)
        )

    subscription = await activate(
        session, payment.user_id, payment.plan_code, source="payment", payment_id=payment.id
    )
    metrics.PAYMENTS.labels(payment.provider, "paid").inc()
    metrics.REVENUE.labels(payment.provider, payment.currency).inc(payment.amount)
    await audit(
        session,
        "admin" if admin_id else "provider",
        admin_id,
        "payment.paid",
        "payment",
        str(payment.id),
        {
            "provider": payment.provider,
            "amount": payment.amount,
            "plan": payment.plan_code,
            "subscription_id": subscription.id,
        },
    )
    return subscription


async def reject_payment(
    session: AsyncSession, payment_id: int, admin_id: int, note: str
) -> Payment:
    payment = await locked_payment(session, payment_id)
    if payment.status == "paid":
        raise Conflict("payment is already settled")
    payment.status = "rejected"
    payment.reviewed_by = admin_id
    payment.reviewed_at = datetime.now(UTC)
    payment.note = note[:500]
    await audit(
        session, "admin", admin_id, "payment.rejected", "payment", str(payment.id), {"note": note}
    )
    return payment


async def activate(
    session: AsyncSession,
    user_id: int,
    plan_code: str,
    *,
    source: str,
    payment_id: int | None = None,
    days: int | None = None,
) -> Subscription:
    """Creates or extends the user's subscription.

    Extending the same plan adds a period. Upgrading converts the unused value of the
    current plan into extra days (pro-rate) instead of throwing it away.
    """
    user = (
        await session.scalars(select(User).where(User.id == user_id).with_for_update())
    ).one_or_none()
    if user is None:
        raise NotFound("user not found")
    plan = await session.get(Plan, plan_code)
    if plan is None:
        raise InvalidInput("unknown plan", plan=plan_code)

    now = datetime.now(UTC)
    period_days = days if days is not None else (plan.period_days or 30)
    live = (
        await session.scalars(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(("active", "trialing", "grace")),
            )
            .with_for_update()
        )
    ).one_or_none()

    start = now
    extra = timedelta(days=period_days)
    if live is not None:
        remaining = max(live.expires_at - now, timedelta(0))
        if live.plan_code == plan_code:
            start = live.expires_at if live.expires_at > now else now
        else:
            # Pro-rate: value left on the old plan, expressed in days of the new one.
            old_plan = await session.get(Plan, live.plan_code)
            old_price = int((old_plan.prices if old_plan else {}).get("IRR", 0) or 0)
            new_price = int(plan.prices.get("IRR", 0) or 0)
            old_period = (old_plan.period_days if old_plan else 30) or 30
            if old_price > 0 and new_price > 0:
                credit_value = (remaining.days * old_price) / old_period
                extra += timedelta(days=credit_value * (plan.period_days or 30) / new_price)
        live.status = "canceled"
        live.canceled_at = now

    subscription = Subscription(
        user_id=user_id,
        plan_code=plan_code,
        status="active",
        source=source,
        payment_id=payment_id,
        started_at=start,
        expires_at=start + extra,
        auto_renew=False,
    )
    session.add(subscription)
    await session.flush()

    user.plan_code = plan_code
    user.premium_until = subscription.expires_at
    plan_service.clear_caches()
    metrics.SUBSCRIPTIONS.labels("activated", source).inc()
    log.info(
        "subscription.activated",
        user_id=user_id,
        plan=plan_code,
        source=source,
        expires_at=subscription.expires_at.isoformat(),
    )
    return subscription


async def start_trial(session: AsyncSession, user_id: int) -> Subscription:
    user = (
        await session.scalars(select(User).where(User.id == user_id).with_for_update())
    ).one_or_none()
    if user is None:
        raise NotFound("user not found")
    if user.trial_used:
        raise Forbidden("trial already used", reason="trial_used")
    live = await session.scalar(
        select(Subscription.id).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(("active", "trialing", "grace")),
        )
    )
    if live is not None:
        raise Conflict("already subscribed")

    days = int(await get_setting(session, "trial_days", TRIAL_DEFAULT_DAYS))
    subscription = await activate(session, user_id, "pro_monthly", source="trial", days=days)
    subscription.status = "trialing"
    user.trial_used = True
    await audit(
        session,
        "user",
        user_id,
        "subscription.trial",
        "subscription",
        str(subscription.id),
        {"days": days},
    )
    return subscription


async def reward_referral(session: AsyncSession, referee_id: int) -> bool:
    """Grants the referrer their reward once the referee qualifies."""
    referral = (
        await session.scalars(
            select(Referral).where(Referral.referee_id == referee_id).with_for_update()
        )
    ).one_or_none()
    if referral is None or referral.status != "pending":
        return False
    days = int(await get_setting(session, "referral_reward_days", REFERRAL_DEFAULT_DAYS))
    await activate(session, referral.referrer_id, "pro_monthly", source="referral", days=days)
    referral.status = "rewarded"
    referral.reward_days = days
    referral.qualified_at = datetime.now(UTC)
    await audit(
        session,
        "system",
        None,
        "subscription.referral_reward",
        "user",
        str(referral.referrer_id),
        {"referee_id": referee_id, "days": days},
    )
    return True


async def gift(
    session: AsyncSession, admin_id: int, user_id: int, days: int, plan_code: str = "pro_monthly"
) -> Subscription:
    subscription = await activate(session, user_id, plan_code, source="gift", days=days)
    await audit(
        session,
        "admin",
        admin_id,
        "subscription.gift",
        "user",
        str(user_id),
        {"days": days, "plan": plan_code},
    )
    return subscription


async def current(session: AsyncSession, user_id: int) -> Subscription | None:
    return (
        await session.scalars(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(("active", "trialing", "grace")),
            )
            .order_by(Subscription.expires_at.desc())
        )
    ).first()


async def expire_due(session: AsyncSession) -> tuple[int, int]:
    """Moves expired subscriptions into grace, then downgrades after the grace period.

    Returns (moved to grace, downgraded).
    """
    grace_days = int(await get_setting(session, "grace_days", GRACE_DEFAULT_DAYS))
    now = datetime.now(UTC)
    to_grace = await session.execute(
        update(Subscription)
        .where(
            Subscription.status.in_(("active", "trialing")),
            Subscription.expires_at <= now,
        )
        .values(status="grace", grace_until=now + timedelta(days=grace_days))
        .returning(Subscription.user_id)
    )
    graced = [row[0] for row in to_grace]

    expired = await session.execute(
        update(Subscription)
        .where(Subscription.status == "grace", Subscription.grace_until <= now)
        .values(status="expired")
        .returning(Subscription.user_id)
    )
    downgraded = [row[0] for row in expired]
    if downgraded:
        await session.execute(
            update(User).where(User.id.in_(downgraded)).values(plan_code="free", premium_until=None)
        )
        for user_id in downgraded:
            await audit(session, "system", None, "subscription.expired", "user", str(user_id), {})
        metrics.SUBSCRIPTIONS.labels("expired", "system").inc(len(downgraded))
    if graced or downgraded:
        plan_service.clear_caches()
    return len(graced), len(downgraded)


async def pending_card_payment(session: AsyncSession, user_id: int) -> Payment | None:
    """The card-to-card payment a receipt photo should be attached to."""
    return (
        await session.scalars(
            select(Payment)
            .where(
                Payment.user_id == user_id,
                Payment.provider == "card2card",
                Payment.status == "pending_review",
            )
            .order_by(Payment.created_at.desc())
        )
    ).first()


async def attach_receipt(session: AsyncSession, payment: Payment, file_id: str) -> Payment:
    payment.receipt_file_id = file_id
    ttl = int(await get_setting(session, "payment_review_ttl_hours", REVIEW_TTL_HOURS))
    payment.review_due_at = datetime.now(UTC) + timedelta(hours=ttl)
    await audit(
        session,
        "user",
        payment.user_id,
        "payment.receipt",
        "payment",
        str(payment.id),
        {"provider": "card2card"},
    )
    return payment


async def refund(
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    payment_id: int,
    *,
    admin_id: int | None = None,
    call_provider: bool = True,
) -> Payment:
    """Marks a payment refunded and cancels the subscription it paid for."""
    payment = await locked_payment(session, payment_id)
    if payment.status != "paid":
        raise Conflict("only a paid payment can be refunded")
    user_tg_id = await session.scalar(select(User.tg_id).where(User.id == payment.user_id))
    if call_provider and payment.provider_ref and user_tg_id is not None:
        provider = await registry.build(session, payment.provider, http, settings)
        try:
            await provider.refund(payment.provider_ref, payment.amount, int(user_tg_id))
        except ProviderError as exc:
            log.warning("payment.refund_failed", payment_id=payment.id, error=str(exc))

    payment.status = "refunded"
    if admin_id is not None:
        payment.reviewed_by = admin_id
        payment.reviewed_at = datetime.now(UTC)
    await session.execute(
        update(Subscription)
        .where(Subscription.payment_id == payment.id, Subscription.status != "refunded")
        .values(status="refunded", canceled_at=datetime.now(UTC))
    )
    await session.execute(
        update(User).where(User.id == payment.user_id).values(plan_code="free", premium_until=None)
    )
    plan_service.clear_caches()
    await audit(
        session,
        "admin" if admin_id else "provider",
        admin_id,
        "payment.refunded",
        "payment",
        str(payment.id),
        {"provider": payment.provider, "amount": payment.amount},
    )
    return payment


async def expire_stale_reviews(session: AsyncSession) -> list[int]:
    """Card-to-card requests nobody reviewed in time. Returns the affected user ids."""
    rows = await session.execute(
        update(Payment)
        .where(
            Payment.provider == "card2card",
            Payment.status == "pending_review",
            Payment.review_due_at <= datetime.now(UTC),
            Payment.receipt_file_id.is_(None),
        )
        .values(status="expired")
        .returning(Payment.user_id)
    )
    return [row[0] for row in rows]


async def due_for_notice(session: AsyncSession) -> list[tuple[int, int, int, str]]:
    """Subscriptions that need a "your plan ends in N days" message.

    Returns ``(subscription_id, user_tg_id, days, lang)``. Already-sent notices are
    filtered out by ``subscription_notices`` so a re-run never repeats a message.
    """
    now = datetime.now(UTC)
    out: list[tuple[int, int, int, str]] = []
    for days in (7, 3, 1):
        kind = f"d{days}"
        rows = await session.execute(
            select(Subscription.id, User.tg_id, User.lang)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status.in_(("active", "trialing")),
                Subscription.expires_at > now,
                Subscription.expires_at <= now + timedelta(days=days),
                User.bot_blocked.is_(False),
                ~select(SubscriptionNotice.subscription_id)
                .where(
                    SubscriptionNotice.subscription_id == Subscription.id,
                    SubscriptionNotice.kind == kind,
                )
                .exists(),
            )
        )
        out.extend((row[0], row[1], days, row[2]) for row in rows)
    return out


async def mark_notice_sent(session: AsyncSession, subscription_id: int, kind: str) -> None:
    await session.execute(
        insert(SubscriptionNotice)
        .values(subscription_id=subscription_id, kind=kind)
        .on_conflict_do_nothing()
    )
