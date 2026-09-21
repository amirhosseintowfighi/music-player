"""Card-to-card.

There is no gateway: the user transfers the amount to a card number shown by the bot
and sends a photo of the receipt. The payment sits in ``pending_review`` until an admin
approves or rejects it, so "verify" here means "an admin said yes" — done in
``app.services.subscriptions``, never automatically.
"""

from __future__ import annotations

from typing import Any

from app.services.payments.base import (
    Invoice,
    InvoiceRequest,
    ProviderContext,
    VerificationResult,
)


class CardToCard:
    code = "card2card"
    currency = "IRR"
    kind = "instructions"

    def __init__(self, context: ProviderContext) -> None:
        self.config = context.config

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        """Returns the transfer instructions; card details come from admin settings."""
        return Invoice(
            kind="instructions",
            provider_ref=f"c2c-{request.public_id}",
            payload={
                "card_number": self.config.get("card_number", ""),
                "holder_name": self.config.get("holder_name", ""),
                "bank": self.config.get("bank", ""),
                "amount": request.amount,
                "reference": request.public_id[:8],
            },
        )

    async def verify(self, callback: dict[str, Any]) -> VerificationResult:
        # Only an admin decision can settle a card-to-card payment.
        return VerificationResult(False, detail="awaiting admin review")

    async def refund(self, provider_ref: str, amount: int, user_tg_id: int) -> bool:
        return False  # refunds are handled by a human transfer
