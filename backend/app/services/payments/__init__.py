"""Provider registry. Enabled providers and their config come from the database."""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import InvalidInput
from app.models import PaymentProviderRow
from app.services.payments.base import (
    Invoice,
    InvoiceRequest,
    PaymentProvider,
    ProviderContext,
    ProviderError,
    VerificationResult,
)
from app.services.payments.card import CardToCard
from app.services.payments.iranian import IdPay, NextPay, Zarinpal
from app.services.payments.stars import TelegramStars

BUILDERS: dict[str, type[PaymentProvider]] = {
    "stars": TelegramStars,
    "zarinpal": Zarinpal,
    "idpay": IdPay,
    "nextpay": NextPay,
    "card2card": CardToCard,
}


async def enabled_providers(session: AsyncSession) -> list[PaymentProviderRow]:
    rows = await session.scalars(
        select(PaymentProviderRow)
        .where(PaymentProviderRow.is_enabled)
        .order_by(PaymentProviderRow.position)
    )
    return [row for row in rows.all() if row.code in BUILDERS]


async def build(
    session: AsyncSession, code: str, http: httpx.AsyncClient, settings: Settings
) -> PaymentProvider:
    row = await session.get(PaymentProviderRow, code)
    if row is None or not row.is_enabled or row.code not in BUILDERS:
        raise InvalidInput("unknown payment provider", provider=code)
    context = ProviderContext(http=http, settings=settings, config=dict(row.config))
    provider: PaymentProvider = BUILDERS[row.code](context)
    return provider


__all__ = [
    "BUILDERS",
    "Invoice",
    "InvoiceRequest",
    "PaymentProvider",
    "ProviderContext",
    "ProviderError",
    "VerificationResult",
    "build",
    "enabled_providers",
]
