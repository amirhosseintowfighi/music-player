"""Telegram Stars (XTR).

Stars are paid inside Telegram: we do not redirect anywhere, we hand the bot the
invoice parameters. The flow is
``sendInvoice`` → ``pre_checkout_query`` (must be answered within 10 seconds) →
``successful_payment`` → (possibly) ``refunded_payment``.

The per-payment ``public_id`` travels as the invoice payload, which is how a
``successful_payment`` update is matched back to its row.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.payments.base import (
    Invoice,
    InvoiceRequest,
    ProviderContext,
    ProviderError,
    VerificationResult,
)
from tmusic_common.logging import get_logger

log = get_logger(__name__)


class TelegramStars:
    code = "stars"
    currency = "XTR"
    kind = "telegram_invoice"

    def __init__(self, context: ProviderContext) -> None:
        self.http = context.http
        self.settings = context.settings
        self.config = context.config

    def _api(self, method: str) -> str:
        token = self.settings.bot_token.get_secret_value()
        return f"{self.settings.tg_api_base.rstrip('/')}/bot{token}/{method}"

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        """Creates an invoice link the Mini App opens with ``openInvoice``.

        Stars invoices carry no provider token and no currency conversion; the amount
        is the number of Stars. ``subscription_period`` (2592000 s) turns it into a
        recurring Stars subscription — Telegram only allows that exact value.
        """
        if request.currency != "XTR":
            raise ProviderError("stars: only XTR is supported")
        params: dict[str, Any] = {
            "title": request.title[:32],
            "description": request.description[:255],
            "payload": request.public_id,
            "currency": "XTR",
            "prices": [{"label": request.title[:32], "amount": request.amount}],
        }
        period = self.config.get("subscription_period")
        if period:
            params["subscription_period"] = int(period)
        try:
            response = await self.http.post(
                self._api("createInvoiceLink"), json=params, timeout=20.0
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("stars: createInvoiceLink failed") from exc
        if not body.get("ok") or not body.get("result"):
            raise ProviderError(f"stars: {body.get('description')}")
        return Invoice(
            kind="telegram_invoice", provider_ref=None, url=str(body["result"]), payload=params
        )

    async def verify(self, callback: dict[str, Any]) -> VerificationResult:
        """Verifies a ``successful_payment`` update.

        Telegram delivers this over the authenticated webhook, so the update itself is
        the proof — there is nothing to call back to.
        """
        charge_id = str(callback.get("telegram_payment_charge_id") or "")
        if not charge_id:
            return VerificationResult(False, detail="missing charge id")
        amount = int(callback.get("total_amount") or 0)
        if str(callback.get("currency") or "XTR") != "XTR":
            return VerificationResult(False, provider_ref=charge_id, detail="wrong currency")
        return VerificationResult(True, provider_ref=charge_id, amount=amount)

    async def refund(self, provider_ref: str, amount: int, user_tg_id: int) -> bool:
        """Stars can be refunded through the Bot API within a limited window."""
        url = self._api("refundStarPayment")
        try:
            response = await self.http.post(
                url,
                json={"user_id": user_tg_id, "telegram_payment_charge_id": provider_ref},
                timeout=20.0,
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("stars: refund call failed") from exc
        if not body.get("ok"):
            log.warning("payment.stars_refund_failed", detail=body.get("description"))
            return False
        return True
