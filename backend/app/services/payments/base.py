"""Payment provider port (ADR-0008).

Adding a gateway means adding one adapter module and one row in ``payment_providers``;
nothing in the core changes. Every provider must:

- create an invoice and return where the user should be sent (or which Telegram
  invoice to show),
- verify a callback **by calling the gateway itself** — never by trusting callback
  parameters,
- be idempotent: the same ``provider_ref`` may arrive twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import Settings


@dataclass(frozen=True, slots=True)
class InvoiceRequest:
    payment_id: int
    public_id: str
    amount: int
    currency: str
    plan_code: str
    title: str
    description: str
    user_tg_id: int
    callback_url: str


@dataclass(frozen=True, slots=True)
class Invoice:
    """Where to send the user.

    ``kind`` is 'redirect' for web gateways, 'telegram_invoice' for Stars (the payload
    is handed to the bot), or 'instructions' for card-to-card.
    """

    kind: str
    provider_ref: str | None = None
    url: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    paid: bool
    provider_ref: str | None = None
    amount: int | None = None
    detail: str = ""


class PaymentProvider(Protocol):
    code: str
    currency: str
    kind: str  # how the client presents it: telegram_invoice | redirect | instructions

    def __init__(self, context: ProviderContext) -> None: ...

    async def create_invoice(self, request: InvoiceRequest) -> Invoice: ...

    async def verify(self, callback: dict[str, Any]) -> VerificationResult: ...

    async def refund(self, provider_ref: str, amount: int, user_tg_id: int) -> bool: ...


class ProviderError(Exception):
    """The gateway refused or misbehaved; the payment stays unpaid."""


@dataclass
class ProviderContext:
    """Everything an adapter needs: HTTP client, settings and the DB-stored config."""

    http: httpx.AsyncClient
    settings: Settings
    config: dict[str, Any]
