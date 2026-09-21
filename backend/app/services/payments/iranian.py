"""Iranian gateways: Zarinpal, IDPay and NextPay.

All three follow the same shape — create a payment, send the user to a page, then
verify server-side with the gateway — so they share one base class and differ only in
their endpoints and field names. Merchant credentials come from the environment; the
non-secret settings (sandbox flag, callback host) come from ``payment_providers.config``.

Amounts are stored in the smallest unit (rial). Zarinpal and NextPay expect rial;
IDPay expects rial as well, which is why no conversion happens here.
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
TIMEOUT_S = 20.0


class IranianGateway:
    code = "iranian"
    currency = "IRR"
    kind = "redirect"

    def __init__(self, context: ProviderContext) -> None:
        self.http = context.http
        self.settings = context.settings
        self.config = context.config

    @property
    def merchant(self) -> str:
        key = self.config.get("merchant_env", "")
        value = self.settings.provider_secret(self.code)
        if not value:
            raise ProviderError(f"{self.code}: merchant credential is not configured ({key})")
        return value

    async def _post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> Any:
        try:
            response = await self.http.post(url, json=payload, headers=headers, timeout=TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.code}: gateway unreachable") from exc
        if response.status_code >= 500:
            raise ProviderError(f"{self.code}: gateway returned {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.code}: invalid response") from exc

    async def create_invoice(
        self, request: InvoiceRequest
    ) -> Invoice:  # pragma: no cover - abstract
        raise NotImplementedError

    async def verify(self, callback: dict[str, Any]) -> VerificationResult:  # pragma: no cover
        raise NotImplementedError

    async def refund(self, provider_ref: str, amount: int, user_tg_id: int) -> bool:
        # None of the three supports API refunds on a standard merchant account.
        log.info("payment.refund_manual", provider=self.code, ref=provider_ref, amount=amount)
        return False


class Zarinpal(IranianGateway):
    code = "zarinpal"

    @property
    def base(self) -> str:
        return (
            "https://sandbox.zarinpal.com/pg/v4/payment"
            if self.config.get("sandbox")
            else "https://payment.zarinpal.com/pg/v4/payment"
        )

    @property
    def start_pay(self) -> str:
        return (
            "https://sandbox.zarinpal.com/pg/StartPay"
            if self.config.get("sandbox")
            else "https://payment.zarinpal.com/pg/StartPay"
        )

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        data = await self._post(
            f"{self.base}/request.json",
            {
                "merchant_id": self.merchant,
                "amount": request.amount,
                "description": request.description[:255],
                "callback_url": request.callback_url,
                "metadata": {"order_id": request.public_id},
            },
        )
        payload = data.get("data") or {}
        if payload.get("code") != 100 or not payload.get("authority"):
            raise ProviderError(f"zarinpal: {data.get('errors') or payload}")
        authority = str(payload["authority"])
        return Invoice(kind="redirect", provider_ref=authority, url=f"{self.start_pay}/{authority}")

    async def verify(self, callback: dict[str, Any]) -> VerificationResult:
        authority = str(callback.get("Authority") or callback.get("authority") or "")
        status = str(callback.get("Status") or callback.get("status") or "")
        amount = int(callback.get("amount") or 0)
        if not authority:
            return VerificationResult(False, detail="missing authority")
        if status.upper() != "OK":
            return VerificationResult(False, provider_ref=authority, detail="user cancelled")
        data = await self._post(
            f"{self.base}/verify.json",
            {"merchant_id": self.merchant, "amount": amount, "authority": authority},
        )
        payload = data.get("data") or {}
        # 100 = verified now, 101 = already verified (a repeated callback).
        if payload.get("code") in (100, 101):
            return VerificationResult(
                True, provider_ref=authority, amount=amount, detail=str(payload.get("ref_id", ""))
            )
        return VerificationResult(
            False, provider_ref=authority, detail=str(data.get("errors") or payload)
        )


class IdPay(IranianGateway):
    code = "idpay"

    @property
    def headers(self) -> dict[str, str]:
        head = {"X-API-KEY": self.merchant, "Content-Type": "application/json"}
        if self.config.get("sandbox"):
            head["X-SANDBOX"] = "1"
        return head

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        data = await self._post(
            "https://api.idpay.ir/v1.1/payment",
            {
                "order_id": request.public_id,
                "amount": request.amount,
                "callback": request.callback_url,
                "desc": request.description[:255],
            },
            headers=self.headers,
        )
        if not data.get("id") or not data.get("link"):
            raise ProviderError(f"idpay: {data}")
        return Invoice(kind="redirect", provider_ref=str(data["id"]), url=str(data["link"]))

    async def verify(self, callback: dict[str, Any]) -> VerificationResult:
        payment_id = str(callback.get("id") or "")
        order_id = str(callback.get("order_id") or "")
        if not payment_id or not order_id:
            return VerificationResult(False, detail="missing id/order_id")
        if str(callback.get("status") or "") not in ("10", "100", "101", "200"):
            return VerificationResult(False, provider_ref=payment_id, detail="not paid")
        data = await self._post(
            "https://api.idpay.ir/v1.1/payment/verify",
            {"id": payment_id, "order_id": order_id},
            headers=self.headers,
        )
        # 100 = verified, 101 = already verified.
        if int(data.get("status") or 0) in (100, 101):
            return VerificationResult(
                True,
                provider_ref=payment_id,
                amount=int(data.get("amount") or 0),
                detail=str((data.get("payment") or {}).get("track_id", "")),
            )
        return VerificationResult(False, provider_ref=payment_id, detail=str(data))


class NextPay(IranianGateway):
    code = "nextpay"

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        data = await self._post(
            "https://nextpay.org/nx/gateway/token",
            {
                "api_key": self.merchant,
                "order_id": request.public_id,
                "amount": request.amount,
                "callback_uri": request.callback_url,
                "currency": "IRR",
            },
        )
        if int(data.get("code", -1)) != -1 or not data.get("trans_id"):
            raise ProviderError(f"nextpay: {data}")
        trans_id = str(data["trans_id"])
        return Invoice(
            kind="redirect",
            provider_ref=trans_id,
            url=f"https://nextpay.org/nx/gateway/payment/{trans_id}",
        )

    async def verify(self, callback: dict[str, Any]) -> VerificationResult:
        trans_id = str(callback.get("trans_id") or "")
        amount = int(callback.get("amount") or 0)
        if not trans_id:
            return VerificationResult(False, detail="missing trans_id")
        data = await self._post(
            "https://nextpay.org/nx/gateway/verify",
            {"api_key": self.merchant, "trans_id": trans_id, "amount": amount, "currency": "IRR"},
        )
        # 0 = verified now, -49 = already verified.
        if int(data.get("code", 1)) in (0, -49):
            return VerificationResult(
                True,
                provider_ref=trans_id,
                amount=amount,
                detail=str(data.get("Shaparak_Ref_Id", "")),
            )
        return VerificationResult(False, provider_ref=trans_id, detail=str(data))
