"""Gateway adapters, tested against recorded responses instead of live endpoints."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.payments.base import InvoiceRequest, ProviderContext, ProviderError
from app.services.payments.card import CardToCard
from app.services.payments.iranian import IdPay, NextPay, Zarinpal
from app.services.payments.stars import TelegramStars

REQUEST = InvoiceRequest(
    payment_id=1,
    public_id="11111111-2222-3333-4444-555555555555",
    amount=1_490_000,
    currency="IRR",
    plan_code="pro_monthly",
    title="Pro monthly",
    description="Pro monthly (30 days)",
    user_tg_id=555,
    callback_url="https://api.example.test/v1/payments/callback/zarinpal",
)


def context(settings: Settings, handler: Any, **config: Any) -> ProviderContext:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # Credentials come from the environment in production; the fixture settings have none.
    configured = settings.model_copy(
        update={
            "zarinpal_merchant_id": SecretStr("merchant-1"),
            "idpay_api_key": SecretStr("idpay-key"),
            "nextpay_api_key": SecretStr("nextpay-key"),
        }
    )
    return ProviderContext(http=http, settings=configured, config=config)


def responder(routes: dict[str, Any]) -> Any:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.append({"url": str(request.url), "json": body and json.loads(body)})
        for fragment, payload in routes.items():
            if fragment in str(request.url):
                if isinstance(payload, int):
                    return httpx.Response(payload)
                return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected call to {request.url}")

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


async def test_zarinpal_create_and_verify(settings: Settings) -> None:
    handler = responder(
        {
            "request.json": {
                "data": {"code": 100, "authority": "A0000000000000000000000000000123"}
            },
            "verify.json": {"data": {"code": 100, "ref_id": 987654}},
        }
    )
    gw = Zarinpal(context(settings, handler, sandbox=True))
    invoice = await gw.create_invoice(REQUEST)
    assert invoice.kind == "redirect"
    assert invoice.url is not None and invoice.url.endswith("A0000000000000000000000000000123")
    assert "sandbox.zarinpal.com" in invoice.url

    result = await gw.verify(
        {"Authority": "A0000000000000000000000000000123", "Status": "OK", "amount": 1_490_000}
    )
    assert result.paid and result.amount == 1_490_000 and result.detail == "987654"


async def test_zarinpal_repeat_callback_is_accepted(settings: Settings) -> None:
    """Code 101 means "already verified" — a retried callback must still settle."""
    gw = Zarinpal(context(settings, responder({"verify.json": {"data": {"code": 101}}})))
    result = await gw.verify({"Authority": "A1", "Status": "OK", "amount": 10})
    assert result.paid


async def test_zarinpal_user_cancelled_never_calls_verify(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("verify must not be called for a cancelled payment")

    gw = Zarinpal(context(settings, handler))
    result = await gw.verify({"Authority": "A1", "Status": "NOK"})
    assert not result.paid and result.detail == "user cancelled"


async def test_zarinpal_failed_verify(settings: Settings) -> None:
    gw = Zarinpal(context(settings, responder({"verify.json": {"errors": {"code": -51}}})))
    result = await gw.verify({"Authority": "A1", "Status": "OK", "amount": 10})
    assert not result.paid


async def test_missing_merchant_credential_is_an_error(settings: Settings) -> None:
    gw = Zarinpal(context(settings, responder({})))
    gw.settings = settings.model_copy(update={"zarinpal_merchant_id": SecretStr("")})
    with pytest.raises(ProviderError, match="not configured"):
        await gw.create_invoice(REQUEST)


async def test_gateway_5xx_raises(settings: Settings) -> None:
    gw = Zarinpal(context(settings, responder({"request.json": 502})))
    with pytest.raises(ProviderError, match="502"):
        await gw.create_invoice(REQUEST)


async def test_gateway_unreachable_raises(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    gw = Zarinpal(context(settings, handler))
    with pytest.raises(ProviderError, match="unreachable"):
        await gw.create_invoice(REQUEST)


async def test_idpay_flow(settings: Settings) -> None:
    handler = responder(
        {
            "payment/verify": {"status": 100, "amount": 1_490_000, "payment": {"track_id": "42"}},
            "v1.1/payment": {"id": "idp-1", "link": "https://idpay.ir/p/idp-1"},
        }
    )
    gw = IdPay(context(settings, handler, sandbox=True))
    invoice = await gw.create_invoice(REQUEST)
    assert invoice.provider_ref == "idp-1"
    result = await gw.verify({"id": "idp-1", "order_id": REQUEST.public_id, "status": "100"})
    assert result.paid and result.amount == 1_490_000


async def test_idpay_unpaid_status_never_verifies(settings: Settings) -> None:
    gw = IdPay(context(settings, responder({})))
    result = await gw.verify({"id": "idp-1", "order_id": "x", "status": "6"})
    assert not result.paid


async def test_nextpay_flow(settings: Settings) -> None:
    handler = responder(
        {
            "gateway/token": {"code": -1, "trans_id": "nx-1"},
            "gateway/verify": {"code": 0, "Shaparak_Ref_Id": "77"},
        }
    )
    gw = NextPay(context(settings, handler))
    invoice = await gw.create_invoice(REQUEST)
    assert invoice.url == "https://nextpay.org/nx/gateway/payment/nx-1"
    result = await gw.verify({"trans_id": "nx-1", "amount": 1_490_000})
    assert result.paid and result.detail == "77"


async def test_nextpay_already_verified(settings: Settings) -> None:
    gw = NextPay(context(settings, responder({"gateway/verify": {"code": -49}})))
    assert (await gw.verify({"trans_id": "nx-1", "amount": 10})).paid


async def test_iranian_refunds_are_manual(settings: Settings) -> None:
    gw = NextPay(context(settings, responder({})))
    assert await gw.refund("nx-1", 10, 555) is False


async def test_stars_invoice_link(settings: Settings) -> None:
    handler = responder({"createInvoiceLink": {"ok": True, "result": "https://t.me/$abc"}})
    gw = TelegramStars(context(settings, handler, subscription_period=2592000))
    invoice = await gw.create_invoice(replace(REQUEST, currency="XTR", amount=150))
    assert invoice.kind == "telegram_invoice" and invoice.url == "https://t.me/$abc"
    sent = handler.seen[-1]["json"]  # type: ignore[attr-defined]
    assert sent["payload"] == REQUEST.public_id
    assert sent["subscription_period"] == 2592000
    assert sent["prices"] == [{"label": "Pro monthly", "amount": 150}]


async def test_stars_rejects_non_xtr(settings: Settings) -> None:
    gw = TelegramStars(context(settings, responder({})))
    with pytest.raises(ProviderError, match="only XTR"):
        await gw.create_invoice(REQUEST)


async def test_stars_api_error(settings: Settings) -> None:
    handler = responder({"createInvoiceLink": {"ok": False, "description": "BAD"}})
    gw = TelegramStars(context(settings, handler))
    with pytest.raises(ProviderError, match="BAD"):
        await gw.create_invoice(replace(REQUEST, currency="XTR", amount=1))


async def test_stars_verify_checks_currency(settings: Settings) -> None:
    gw = TelegramStars(context(settings, responder({})))
    assert not (await gw.verify({"telegram_payment_charge_id": "", "total_amount": 1})).paid
    assert not (
        await gw.verify({"telegram_payment_charge_id": "c1", "total_amount": 1, "currency": "USD"})
    ).paid
    ok = await gw.verify({"telegram_payment_charge_id": "c1", "total_amount": 150})
    assert ok.paid and ok.provider_ref == "c1" and ok.amount == 150


async def test_stars_refund(settings: Settings) -> None:
    handler = responder({"refundStarPayment": {"ok": True}})
    gw = TelegramStars(context(settings, handler))
    assert await gw.refund("c1", 150, 555) is True
    assert handler.seen[-1]["json"]["user_id"] == 555  # type: ignore[attr-defined]


async def test_card_to_card_never_self_verifies(settings: Settings) -> None:
    gw = CardToCard(
        context(settings, responder({}), card_number="6037-9900-0000-0000", holder_name="Ali")
    )
    invoice = await gw.create_invoice(REQUEST)
    assert invoice.kind == "instructions"
    assert invoice.payload is not None
    assert invoice.payload["card_number"] == "6037-9900-0000-0000"
    assert invoice.payload["reference"] == REQUEST.public_id[:8]
    assert not (await gw.verify({"anything": True})).paid
    assert await gw.refund("x", 1, 2) is False
