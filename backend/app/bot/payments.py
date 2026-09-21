"""Bot side of payments: Telegram Stars and card-to-card receipts.

Stars flow: the Mini App opens an invoice link, Telegram sends ``pre_checkout_query``
(which must be answered within 10 seconds — so the check here is a single indexed
lookup and nothing else), then ``successful_payment`` on the same webhook, which is the
only proof we need. ``refunded_payment`` arrives if the user gets their Stars back.

Card-to-card flow: checkout puts the payment in ``pending_review`` and the bot prints
the card details; the user sends the receipt photo, which is forwarded to the review
group with approve/reject buttons. Only an admin decision activates the plan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import t
from app.config import Settings
from app.errors import AppError
from app.models import AdminUser, Payment, Plan, User
from app.services import subscriptions
from tmusic_common.logging import get_logger

log = get_logger(__name__)

APPROVE = "pay:ok:"
REJECT = "pay:no:"


def _fmt_amount(amount: int, currency: str) -> str:
    if currency == "XTR":
        return f"{amount} ⭐"
    return f"{amount // 10:,} تومان"  # rial -> toman


def _fmt_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


async def _plan_name(session: AsyncSession, plan_code: str, lang: str) -> str:
    plan = await session.get(Plan, plan_code)
    if plan is None:
        return plan_code
    return plan.name_fa if lang == "fa" else plan.name_en


async def on_pre_checkout(query: PreCheckoutQuery, session: AsyncSession) -> None:
    """Answered within 10 seconds or Telegram cancels the payment."""
    payment = (
        await session.scalars(select(Payment).where(Payment.public_id == query.invoice_payload))
    ).one_or_none()
    ok = (
        payment is not None
        and payment.provider == "stars"
        and payment.status in ("created", "pending")
        and payment.amount == query.total_amount
    )
    await query.answer(ok=ok, error_message=None if ok else "This invoice is no longer valid.")
    if not ok:
        log.warning("pay.pre_checkout_rejected", payload=query.invoice_payload)


async def on_successful_payment(
    message: Message, session: AsyncSession, settings: Settings, http: httpx.AsyncClient
) -> None:
    payment_info = message.successful_payment
    if payment_info is None or message.from_user is None:
        return
    user = (
        await session.scalars(select(User).where(User.tg_id == message.from_user.id))
    ).one_or_none()
    lang = user.lang if user else "fa"
    callback: dict[str, Any] = {
        "invoice_payload": payment_info.invoice_payload,
        "telegram_payment_charge_id": payment_info.telegram_payment_charge_id,
        "total_amount": payment_info.total_amount,
        "currency": payment_info.currency,
    }
    try:
        payment = await subscriptions.settle_callback(session, http, settings, "stars", callback)
    except AppError as exc:
        log.error("pay.stars_settle_failed", error=str(exc))
        await message.answer(t("error", lang))
        return
    if payment.status != "paid":
        await message.answer(t("error", lang))
        return
    live = await subscriptions.current(session, payment.user_id)
    await message.answer(
        t(
            "pay_thanks",
            lang,
            plan=await _plan_name(session, payment.plan_code, lang),
            until=_fmt_date(live.expires_at) if live else "—",
        )
    )


async def on_refunded_payment(
    message: Message, session: AsyncSession, settings: Settings, http: httpx.AsyncClient
) -> None:
    refund = message.refunded_payment
    if refund is None or message.from_user is None:
        return
    user = (
        await session.scalars(select(User).where(User.tg_id == message.from_user.id))
    ).one_or_none()
    payment = (
        await session.scalars(
            select(Payment).where(
                Payment.provider == "stars",
                Payment.provider_ref == refund.telegram_payment_charge_id,
            )
        )
    ).one_or_none()
    if payment is None:
        return
    await subscriptions.refund(session, http, settings, payment.id, call_provider=False)
    await message.answer(t("pay_refunded", user.lang if user else "fa"))


async def on_subscription_status(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        return
    user = (
        await session.scalars(select(User).where(User.tg_id == message.from_user.id))
    ).one_or_none()
    if user is None:
        return
    live = await subscriptions.current(session, user.id)
    if live is None:
        await message.answer(t("sub_free", user.lang))
        return
    await message.answer(
        t(
            "sub_active",
            user.lang,
            plan=await _plan_name(session, live.plan_code, user.lang),
            until=_fmt_date(live.expires_at),
        )
    )


async def on_receipt_photo(
    message: Message, session: AsyncSession, settings: Settings, bot: Bot
) -> None:
    """A photo from a user who has a card-to-card payment waiting for a receipt."""
    if message.from_user is None or not message.photo:
        return
    user = (
        await session.scalars(select(User).where(User.tg_id == message.from_user.id))
    ).one_or_none()
    if user is None:
        return
    payment = await subscriptions.pending_card_payment(session, user.id)
    if payment is None:
        await message.answer(t("c2c_no_pending", user.lang))
        return

    await subscriptions.attach_receipt(session, payment, message.photo[-1].file_id)
    hours = 0
    if payment.review_due_at is not None:
        hours = max(int((payment.review_due_at - datetime.now(UTC)).total_seconds() // 3600), 1)
    await message.answer(t("c2c_received", user.lang, hours=hours))

    if not settings.payments_admin_chat_id:
        log.warning("pay.no_review_chat", payment_id=payment.id)
        return
    caption = (
        f"\U0001f4b3 card-to-card #{payment.id}\n"
        f"user: {user.first_name} (id {user.id}, tg {user.tg_id})\n"
        f"plan: {payment.plan_code}\n"
        f"amount: {_fmt_amount(payment.amount, payment.currency)}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"{APPROVE}{payment.id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"{REJECT}{payment.id}"),
            ]
        ]
    )
    await bot.send_photo(
        settings.payments_admin_chat_id,
        message.photo[-1].file_id,
        caption=caption,
        reply_markup=keyboard,
    )


async def _reviewer(session: AsyncSession, tg_id: int) -> AdminUser | None:
    return (
        await session.scalars(
            select(AdminUser).where(AdminUser.tg_id == tg_id, AdminUser.is_active)
        )
    ).one_or_none()


async def on_review_decision(query: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Approve/reject pressed in the review group. Only active admins may decide."""
    data = query.data or ""
    payment_id = int(data.rsplit(":", 1)[1])
    admin = await _reviewer(session, query.from_user.id)
    if admin is None:
        await query.answer("not allowed", show_alert=True)
        return

    approve = data.startswith(APPROVE)
    try:
        if approve:
            payment = await subscriptions.locked_payment(session, payment_id)
            await subscriptions.mark_paid(
                session, payment, provider_ref=payment.provider_ref, admin_id=admin.id
            )
        else:
            payment = await subscriptions.reject_payment(
                session, payment_id, admin.id, "receipt rejected"
            )
    except AppError as exc:
        await query.answer(exc.message, show_alert=True)
        return

    user = await session.get(User, payment.user_id)
    lang = user.lang if user else "fa"
    if user is not None:
        live = await subscriptions.current(session, user.id)
        text = (
            t(
                "pay_thanks",
                lang,
                plan=await _plan_name(session, payment.plan_code, lang),
                until=_fmt_date(live.expires_at) if live else "—",
            )
            if approve
            else t("c2c_rejected", lang, note="")
        )
        try:
            await bot.send_message(user.tg_id, text)
        except Exception:  # noqa: BLE001 - the user may have blocked the bot
            log.info("pay.notify_failed", user_id=user.id)

    await query.answer("done")
    if query.message is not None and isinstance(query.message, Message):
        mark = "✅" if approve else "❌"
        await bot.edit_message_caption(
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            caption=f"{query.message.caption or ''}\n{mark} by {admin.id}",
        )


def register(router: Router) -> None:
    router.pre_checkout_query.register(on_pre_checkout)
    router.message.register(on_successful_payment, F.successful_payment)
    router.message.register(on_refunded_payment, F.refunded_payment)
    router.message.register(on_subscription_status, Command("sub"))
    # a forwarded photo is a channel post, not a receipt
    router.message.register(
        on_receipt_photo, F.chat.type == ChatType.PRIVATE, F.photo, ~F.forward_origin
    )
    router.callback_query.register(
        on_review_decision, F.data.startswith(APPROVE) | F.data.startswith(REJECT)
    )
