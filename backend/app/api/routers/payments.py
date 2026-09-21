"""Plans, checkout, gateway callbacks and the user's own subscription.

The callback endpoints are **unauthenticated** — a gateway calls them, not the user —
so they never trust their input: the payment is looked up by its own id and then
re-verified against the gateway before anything is activated
(:mod:`app.services.subscriptions`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Claims, SessionDep, SettingsDep, State, WritableClaims, rate_limit_ip
from app.errors import AppError, NotFound
from app.models import Payment, Plan, User
from app.schemas import (
    CheckoutIn,
    CheckoutOut,
    DiscountPreviewOut,
    PaymentOut,
    PlanOut,
    PlansOut,
    ProviderOut,
    SubscriptionOut,
)
from app.services import payments as registry
from app.services import subscriptions
from tmusic_common.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["payments"])

FREE = SubscriptionOut(plan_code="free", status="free")


async def _user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    return user


@router.get("/plans", response_model=PlansOut)
async def list_plans(claims: Claims, session: SessionDep) -> PlansOut:
    rows = (await session.scalars(select(Plan).where(Plan.is_active).order_by(Plan.position))).all()
    user = await _user(session, claims.user_id)
    providers = [
        ProviderOut(
            code=row.code,
            currency=row.currency,
            kind=registry.BUILDERS[row.code].kind,
        )
        for row in await registry.enabled_providers(session)
    ]
    trial_days = int(await subscriptions.get_setting(session, "trial_days", 7))
    return PlansOut(
        plans=[
            PlanOut(
                code=plan.code,
                name=plan.name_fa if claims.lang == "fa" else plan.name_en,
                period_days=plan.period_days,
                prices={k: int(v) for k, v in plan.prices.items()},
                limits=dict(plan.limits),
                features=list(plan.features),
                is_current=plan.code == user.plan_code,
            )
            for plan in rows
        ],
        providers=providers,
        trial_available=not user.trial_used and user.plan_code == "free",
        trial_days=trial_days,
    )


@router.post("/payments/checkout", response_model=CheckoutOut)
async def checkout(
    body: CheckoutIn, claims: WritableClaims, session: SessionDep, state: State
) -> CheckoutOut:
    user = await _user(session, claims.user_id)
    result = await subscriptions.start_checkout(
        session,
        state.http,
        state.settings,
        user,
        plan_code=body.plan_code,
        provider_code=body.provider,
        discount_code=body.discount_code,
    )
    return CheckoutOut(
        payment_id=result.public_id,
        provider=result.provider,
        amount=result.amount,
        currency=result.currency,
        kind=result.invoice.kind,
        url=result.invoice.url,
        payload=result.invoice.payload if result.invoice.kind == "instructions" else None,
    )


@router.get("/payments/discount", response_model=DiscountPreviewOut)
async def preview_discount(
    claims: Claims, session: SessionDep, code: str, plan_code: str, currency: str = "IRR"
) -> DiscountPreviewOut:
    plan, price = await subscriptions.price_for(session, plan_code, currency)
    try:
        amount, _ = await subscriptions.apply_discount(
            session, claims.user_id, code, price, plan.code, currency
        )
    except AppError as exc:
        return DiscountPreviewOut(
            valid=False,
            amount=price,
            original_amount=price,
            discount_amount=0,
            currency=currency,
            reason=str(exc.details.get("reason", "invalid_code")),
        )
    return DiscountPreviewOut(
        valid=True,
        amount=amount,
        original_amount=price,
        discount_amount=price - amount,
        currency=currency,
    )


@router.post("/payments/trial", response_model=SubscriptionOut)
async def start_trial(claims: WritableClaims, session: SessionDep) -> SubscriptionOut:
    await subscriptions.start_trial(session, claims.user_id)
    return await _subscription_out(session, claims.user_id)


@router.get("/me/subscription", response_model=SubscriptionOut)
async def my_subscription(claims: Claims, session: SessionDep) -> SubscriptionOut:
    return await _subscription_out(session, claims.user_id)


@router.get("/me/payments", response_model=list[PaymentOut])
async def my_payments(claims: Claims, session: SessionDep) -> list[PaymentOut]:
    rows = (
        await session.scalars(
            select(Payment)
            .where(Payment.user_id == claims.user_id)
            .order_by(Payment.created_at.desc())
            .limit(50)
        )
    ).all()
    return [
        PaymentOut(
            public_id=str(row.public_id),
            provider=row.provider,
            plan_code=row.plan_code,
            amount=row.amount,
            currency=row.currency,
            status=row.status,
            created_at=row.created_at,
            paid_at=row.paid_at,
        )
        for row in rows
    ]


async def _subscription_out(session: AsyncSession, user_id: int) -> SubscriptionOut:
    live = await subscriptions.current(session, user_id)
    if live is None:
        return FREE
    left = live.expires_at - datetime.now(UTC)
    return SubscriptionOut(
        plan_code=live.plan_code,
        status=live.status,
        source=live.source,
        started_at=live.started_at,
        expires_at=live.expires_at,
        grace_until=live.grace_until,
        days_left=max(left.days, 0),
        auto_renew=live.auto_renew,
    )


# Two decorators rather than api_route(methods=[...]): one operation id per verb.
@router.get(
    "/payments/callback/{provider}",
    operation_id="payment_callback_redirect",
    dependencies=[Depends(rate_limit_ip)],
)
@router.post(
    "/payments/callback/{provider}",
    operation_id="payment_callback_post",
    dependencies=[Depends(rate_limit_ip)],
)
async def gateway_callback(
    provider: str,
    request: Request,
    session: SessionDep,
    state: State,
    settings: SettingsDep,
) -> RedirectResponse:
    """Settles a payment and sends the user back into the Mini App.

    Gateways redirect the browser here (Zarinpal, NextPay) or POST here (IDPay), so
    both verbs are accepted. The response is always a redirect: the user must not see
    a JSON body.
    """
    callback: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        try:
            body = await request.json()
        except ValueError:
            body = dict(await request.form())
        if isinstance(body, dict):
            callback.update({str(k): v for k, v in body.items()})

    status = "failed"
    try:
        payment = await subscriptions.settle_callback(
            session, state.http, settings, provider, callback
        )
        status = "ok" if payment.status == "paid" else payment.status
    except AppError as exc:
        log.warning("payment.callback_error", provider=provider, error=str(exc))
    except Exception:  # the user still has to land somewhere sane
        log.exception("payment.callback_crash", provider=provider)

    target = f"https://t.me/{settings.bot_username}/app?startapp=pay_{status}"
    return RedirectResponse(target, status_code=303)
